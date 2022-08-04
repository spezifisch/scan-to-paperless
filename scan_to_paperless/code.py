"""Add the QRCode and the BarCodes to a PDF in an additional page."""

import argparse
import io
import logging
import math
import os
import random
import subprocess  # nosec
import tempfile
from typing import Dict, List, Optional, Set, Tuple, TypedDict

import cv2
import pikepdf
import zxingcpp
from PIL import Image
from PyPDF2 import PdfFileReader, PdfFileWriter
from pyzbar import pyzbar
from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas
from weasyprint import CSS, HTML

_LOG = logging.getLogger(__name__)
Image.MAX_IMAGE_PIXELS = 500000000


class _Code(TypedDict):
    pos: int
    type: str
    data: str


class _AllCodes(TypedDict):
    pages: Set[int]
    pos: int


class _PageCode(TypedDict):
    pos: int
    rect: List[Tuple[float, float]]


def _point(point: Tuple[int, int], deg_angle: float, width: int, height: int) -> Tuple[float, float]:
    assert -90 <= deg_angle <= 90
    angle = math.radians(deg_angle)
    diff_x = 0.0
    diff_y = 0.0
    if deg_angle < 0:
        diff_y = width * math.sin(-angle)
    else:
        diff_x = height * math.sin(angle)
    x = point[0] - diff_x
    y = point[1] - diff_y
    return (
        x * math.cos(angle) + y * math.sin(angle),
        -x * math.sin(angle) + y * math.cos(angle),
    )


def _get_codes_with_open_cv(
    image: str,
    alpha: float,
    page: int,
    width: int,
    height: int,
    all_codes: Optional[List[_Code]] = None,
    added_codes: Optional[Dict[str, _AllCodes]] = None,
) -> List[_PageCode]:

    if added_codes is None:
        added_codes = {}
    if all_codes is None:
        all_codes = []
    codes: List[_PageCode] = []

    decoded_image = cv2.imread(image, flags=cv2.IMREAD_COLOR)
    if decoded_image is not None:
        detector = cv2.QRCodeDetector()
        retval, decoded_info, points, straight_qr_code = detector.detectAndDecodeMulti(decoded_image)
        if retval:
            if os.environ.get("PROGRESS", "FALSE") == "TRUE":
                base_path = os.path.dirname(image)
                filename = ".".join(os.path.basename(image).split(".")[:-1])
                suffix = random.randint(0, 1000)  # nosec
                for img_index, img in enumerate(straight_qr_code):
                    dest_filename = os.path.join(
                        base_path,
                        f"{filename}-qrcode-{page}-{suffix}-{img_index}.png",
                    )
                    cv2.imwrite(dest_filename, img)

            for index, data in enumerate(decoded_info):
                bbox = points[index]
                if bbox is not None and len(data) > 0 and data not in added_codes:
                    pos = len(all_codes)
                    added_codes[data] = {"pages": set(), "pos": pos}
                    all_codes.append(
                        {
                            "type": "QR code",
                            "pos": pos,
                            "data": data,
                        }
                    )
                if page not in added_codes[data]["pages"]:
                    added_codes[data]["pages"].add(page)
                    codes.append(
                        {
                            "pos": added_codes[data]["pos"],
                            "rect": [_point(p, alpha, width, height) for p in bbox],
                        }
                    )
        try:
            detector = cv2.barcode.BarcodeDetector()
            retval, decoded_info, decoded_type, points = detector.detectAndDecode(decoded_image)
            if retval:
                for index, data in enumerate(decoded_info):
                    bbox = points[index]
                    type_ = decoded_type[index]
                    if bbox is not None and len(data) > 0 and data not in added_codes:
                        pos = len(all_codes)
                        added_codes[data] = {"pages": set(), "pos": pos}
                        all_codes.append(
                            {
                                "type": type_[0] + type_[1:].lower(),
                                "pos": pos,
                                "data": data,
                            }
                        )
                    if page not in added_codes[data]["pages"]:
                        added_codes[data]["pages"].add(page)
                        codes.append(
                            {
                                "pos": added_codes[data]["pos"],
                                "rect": [_point(p, alpha, width, height) for p in bbox],
                            }
                        )
        except Exception:
            _LOG.warning("Open CV barcode decoder not available")

    return codes


def _get_codes_with_open_cv_we_chat(
    image: str,
    alpha: float,
    page: int,
    width: int,
    height: int,
    all_codes: Optional[List[_Code]] = None,
    added_codes: Optional[Dict[str, _AllCodes]] = None,
) -> List[_PageCode]:
    del alpha, width, height, page

    if added_codes is None:
        added_codes = {}
    if all_codes is None:
        all_codes = []
    codes: List[_PageCode] = []

    decoded_image = cv2.imread(image, flags=cv2.IMREAD_COLOR)
    if decoded_image is not None:
        detector = cv2.wechat_qrcode_WeChatQRCode()
        try:
            retval, points = detector.detectAndDecode(decoded_image)
            for index, data in enumerate(retval):
                bbox = points[index]
                if bbox is not None and len(data) > 0 and data not in added_codes:
                    pos = len(all_codes)
                    added_codes[data] = {"pages": set(), "pos": pos}
                    all_codes.append(
                        {
                            "type": "QR code",
                            "pos": pos,
                            "data": data,
                        }
                    )
                # In current version of wechat_qrcode, the bounding box are not correct
                # if page not in added_codes[data]['pages']:
                #     added_codes[data]['pages'].add(page)
                #     codes.append(
                #         {
                #             "pos": added_codes[data]['pos'],
                #             "rect": [_point(p, alpha, width, height) for p in bbox],
                #         }
                #     )
        except UnicodeDecodeError as exception:
            _LOG.warning("Open CV wechat QR code decoder error: %s", str(exception))

    return codes


def _get_codes_with_zxing(
    image: str,
    alpha: float,
    page: int,
    width: int,
    height: int,
    all_codes: Optional[List[_Code]] = None,
    added_codes: Optional[Dict[str, _AllCodes]] = None,
) -> List[_PageCode]:
    if added_codes is None:
        added_codes = {}
    if all_codes is None:
        all_codes = []
    codes: List[_PageCode] = []

    decoded_image = cv2.imread(image, flags=cv2.IMREAD_COLOR)
    if decoded_image is not None:
        for result in zxingcpp.read_barcodes(decoded_image):  # pylint: disable=c-extension-no-member
            if result.text not in added_codes:
                pos = len(all_codes)
                added_codes[result.text] = {"pages": set(), "pos": pos}
                all_codes.append(
                    {
                        "type": "QR code" if result.format.name == "QRCode" else result.format.name,
                        "pos": pos,
                        "data": result.text,
                    }
                )
            if page not in added_codes[result.text]["pages"]:
                added_codes[result.text]["pages"].add(page)
                codes.append(
                    {
                        "pos": added_codes[result.text]["pos"],
                        "rect": [
                            _point(p, alpha, width, height)
                            for p in [
                                (result.position.top_left.x, result.position.top_left.y),
                                (result.position.top_right.x, result.position.top_right.y),
                                (result.position.bottom_right.x, result.position.bottom_right.y),
                                (result.position.bottom_left.x, result.position.bottom_left.y),
                            ]
                        ],
                    }
                )

    return codes


def _get_codes_with_z_bar(
    image: str,
    alpha: float,
    page: int,
    width: int,
    height: int,
    all_codes: Optional[List[_Code]] = None,
    added_codes: Optional[Dict[str, _AllCodes]] = None,
) -> List[_PageCode]:

    if added_codes is None:
        added_codes = {}
    if all_codes is None:
        all_codes = []
    codes: List[_PageCode] = []

    img = Image.open(image)
    for output in pyzbar.decode(img):
        if output.data.decode().replace("\\n", "\n") not in added_codes:
            pos = len(all_codes)
            added_codes[output.data.decode().replace("\\n", "\n")] = {"pages": set(), "pos": pos}
            all_codes.append(
                {
                    "type": "QR code"
                    if output.type == "QRCODE"
                    else output.type[0] + output.type[1:].lower(),
                    "pos": pos,
                    "data": output.data.decode().replace("\\n", "\n"),
                }
            )
        if page not in added_codes[output.data.decode().replace("\\n", "\n")]["pages"]:
            added_codes[output.data.decode().replace("\\n", "\n")]["pages"].add(page)
            codes.append(
                {
                    "pos": added_codes[output.data.decode().replace("\\n", "\n")]["pos"],
                    "rect": [_point((p.x, p.y), alpha, width, height) for p in output.polygon],
                }
            )

    return codes


def add_codes(
    input_filename: str,
    output_filename: str,
    dpi: float = 200,
    pdf_dpi: float = 72,
    font_size: float = 16,
    font_name: str = "Helvetica-Bold",
    margin_left: float = 2,
    margin_top: float = 0,
) -> None:
    """Add the QRCode and the BarCodes to a PDF in an additional page."""
    # Codes information to create the new page
    all_codes: List[_Code] = []
    # Codes information about the already found codes
    added_codes: Dict[str, _AllCodes] = {}

    with open(input_filename, "rb") as input_file:
        existing_pdf = PdfFileReader(input_file)
        metadata = {**existing_pdf.metadata}  # type: ignore
        output_pdf = PdfFileWriter()
        for index, page in enumerate(existing_pdf.pages):
            _LOG.info("Processing page %s", index + 1)
            # Get the QR code from the page
            with tempfile.NamedTemporaryFile(suffix=f"-{index}.png") as image_file:
                image = image_file.name
                subprocess.run(  # nosec
                    [
                        "gm",
                        "convert",
                        "-density",
                        str(dpi),
                        f"{input_filename}[{index}]",
                        image,
                    ],
                    check=True,
                )
                img0 = Image.open(image)

                # Codes information to add the mask and number on the page
                codes: List[_PageCode] = []
                codes += _get_codes_with_zxing(
                    image, 0, index, img0.width, img0.height, all_codes, added_codes
                )
                codes += _get_codes_with_open_cv_we_chat(
                    image, 0, index, img0.width, img0.height, all_codes, added_codes
                )
                # codes += _get_codes_with_open_cv(
                #   image, 0, index, img0.width, img0.height, all_codes, added_codes)
                # codes += _get_codes_with_z_bar(
                #   image, 0, index, img0.width, img0.height, all_codes, added_codes)
                # for angle in range(-10, 11, 2):
                #     subprocess.run(  # nosec
                #         [
                #             "gm",
                #             "convert",
                #             "-density",
                #             str(dpi),
                #             "-rotate",
                #             str(angle),
                #             f"{input_filename}[{index}]",
                #             image,
                #         ],
                #         check=True,
                #     )
                #     codes += _get_codes_with_z_bar(
                #         image, angle, page, img0.width, img0.height, all_codes, added_codes
                #     )

                if codes:
                    packet = io.BytesIO()
                    can = canvas.Canvas(
                        packet, pagesize=(page.mediabox.width, page.mediabox.height), bottomup=False
                    )
                    for code in codes:
                        can.setFillColor(Color(1, 1, 1, alpha=0.7))
                        path = can.beginPath()
                        path.moveTo(code["rect"][0][0] / dpi * pdf_dpi, code["rect"][0][1] / dpi * pdf_dpi)
                        for point in code["rect"][1:]:
                            path.lineTo(point[0] / dpi * pdf_dpi, point[1] / dpi * pdf_dpi)
                        path.close()
                        can.drawPath(path, stroke=0, fill=1)

                        can.setFillColorRGB(0, 0, 0)
                        can.setFont(font_name, font_size)
                        can.drawString(
                            min((p[0] for p in code["rect"])) / dpi * pdf_dpi + margin_left,
                            min((p[1] for p in code["rect"])) / dpi * pdf_dpi + font_size + 0 + margin_top,
                            str(code["pos"]),
                        )

                    can.save()
                    # move to the beginning of the StringIO buffer
                    packet.seek(0)

                    # Create a new PDF with Reportlab
                    new_pdf = PdfFileReader(packet)

                    page.mergePage(new_pdf.getPage(0))
                output_pdf.addPage(page)

        if all_codes:
            _LOG.info("%s codes found, create the additional page", len(all_codes))
            with tempfile.NamedTemporaryFile(suffix=".pdf") as dest_1, tempfile.NamedTemporaryFile(
                suffix=".pdf"
            ) as dest_2:
                # Finally, write "output" to a real file

                with open(dest_1.name, "wb") as output_stream:
                    output_pdf.write(output_stream)

                for code_ in all_codes:
                    data = code_["data"].split("\r\n")
                    if len(data) == 1:
                        data = data[0].split("\n")
                    data = [d if d else "|" for d in data]
                    code_["data_formatted"] = "<br />".join(data)  # type: ignore
                sections = [
                    f"<h2>{code_['type']} [{code_['pos']}]</h2>"
                    f"<p>{code_['data_formatted']}</p>"  # type: ignore
                    for code_ in all_codes
                ]

                html = HTML(
                    string=f"""<html>
                <head>
                    <meta charset="utf-8">
                </head>
                <body>
                    <section id="heading">
                        <p>QR code and Barcode</p>
                    </section>
                    {'<hr />'.join(sections)}
                </body>
                </html>"""
                )

                css = CSS(string="@page { size: A4; margin: 1.5cm } p { font-size: 5pt; font-family: sans; }")

                html.write_pdf(dest_2.name, stylesheets=[css])

                subprocess.run(  # nosec
                    ["pdftk", dest_1.name, dest_2.name, "output", output_filename, "compress"], check=True
                )

                if metadata:
                    with pikepdf.open(output_filename, allow_overwriting_input=True) as pdf:
                        with pdf.open_metadata() as meta:
                            formatted_codes = "\n-\n".join(
                                [f"{code_['type']} [{code_['pos']}]\n{code_['data']}" for code_ in all_codes]
                            )
                            if meta.get("{http://purl.org/dc/elements/1.1/}description"):
                                meta["{http://purl.org/dc/elements/1.1/}description"] += (
                                    "\n-\n" + formatted_codes
                                )
                            else:
                                meta["{http://purl.org/dc/elements/1.1/}description"] = formatted_codes
                        for key, value in metadata.items():
                            pdf.docinfo[key] = value
                        pdf.docinfo["/Codes"] = "\n-\n".join(
                            [f"{code_['type']} [{code_['pos']}]\n{code_['data']}" for code_ in all_codes]
                        )
                        pdf.save(output_filename)
        else:
            _LOG.info("No codes found, copy the input file")
            subprocess.run(["cp", input_filename, output_filename], check=True)  # nosec


def main() -> None:
    """Add the QRCode and the BarCodes to a PDF in an additional page."""
    arg_parser = argparse.ArgumentParser("Add the QRCode and the BarCodes to a PDF in an additional page")
    arg_parser.add_argument(
        "--dpi",
        help="The DPI used in the intermediate image to detect the QR code and the BarCode",
        type=int,
        default=300,
    )
    arg_parser.add_argument("--pdf-dpi", help="The DPI used in the PDF", type=int, default=72)
    arg_parser.add_argument(
        "--font-size", help="The font size used in the PDF to add the number", type=int, default=10
    )
    arg_parser.add_argument(
        "--margin-left", help="The margin left used in the PDF to add the number", type=int, default=2
    )
    arg_parser.add_argument(
        "--margin-top", help="The margin top used in the PDF to add the number", type=int, default=0
    )
    arg_parser.add_argument("input_filename", help="The input PDF filename")
    arg_parser.add_argument("output_filename", help="The output PDF filename")
    args = arg_parser.parse_args()

    add_codes(
        args.input_filename,
        args.output_filename,
        args.dpi,
        args.pdf_dpi,
        args.font_size,
        args.margin_left,
        args.margin_top,
    )

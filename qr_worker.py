import json
import sys
import cv2
import numpy as np


def _decode_opencv(img):
    if img is None or img.size == 0:
        return ""
    for eps in (0.20, 0.35):
        detector = cv2.QRCodeDetector()
        try:
            detector.setEpsX(eps)
            detector.setEpsY(eps)
        except Exception:
            pass
        try:
            text, _, _ = detector.detectAndDecode(img)
            if text:
                return text.strip()
        except cv2.error:
            pass
    return ""


def _decode_zxing(img):
    """ZXing-cpp costuma ser mais tolerante a QR pequeno/reduzido em A5."""
    try:
        import zxingcpp
    except Exception:
        return ""
    try:
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB) if img.ndim == 3 else img
        resultados = zxingcpp.read_barcodes(rgb)
        for item in resultados or []:
            texto = str(getattr(item, "text", "") or "").strip()
            if texto:
                return texto
    except Exception:
        return ""
    return ""


def _decode_pyzbar(img):
    try:
        from pyzbar.pyzbar import decode as zbar_decode
    except Exception:
        return ""
    try:
        resultados = zbar_decode(img)
        for item in resultados or []:
            dados = getattr(item, "data", b"")
            if dados:
                return dados.decode("utf-8", errors="ignore").strip()
    except Exception:
        pass
    return ""


def _variantes(region):
    """Gera poucas variantes úteis, preservando resolução do QR A5."""
    if region is None or region.size == 0:
        return
    # O crop é feito ANTES de qualquer redução. Assim um cartão A5 não perde
    # módulos do QR só porque a fotografia inteira é grande.
    h, w = region.shape[:2]
    maior = max(h, w)
    if maior > 1500:
        s = 1500.0 / maior
        region = cv2.resize(region, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)

    region = cv2.copyMakeBorder(
        region, 28, 28, 28, 28, cv2.BORDER_CONSTANT, value=(255, 255, 255)
    )

    for angle in (0, 90, 270, 180):
        if angle == 0:
            rot = region
        elif angle == 90:
            rot = cv2.rotate(region, cv2.ROTATE_90_CLOCKWISE)
        elif angle == 180:
            rot = cv2.rotate(region, cv2.ROTATE_180)
        else:
            rot = cv2.rotate(region, cv2.ROTATE_90_COUNTERCLOCKWISE)

        for scale in (1.0, 1.8, 2.6):
            cand = rot if scale == 1.0 else cv2.resize(
                rot, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
            )
            yield cand

            gray = cv2.cvtColor(cand, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=1.7, tileGridSize=(8, 8)).apply(gray)
            yield clahe

            # Nitidez leve ajuda quando o A4 foi reduzido fisicamente para A5.
            blur = cv2.GaussianBlur(clahe, (0, 0), 1.0)
            sharp = cv2.addWeighted(clahe, 1.7, blur, -0.7, 0)
            yield sharp

            _, otsu = cv2.threshold(
                sharp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )
            yield otsu


def decode(path):
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        return ""

    h, w = img.shape[:2]

    # Regiões progressivas do topo direito. Funcionam com A4, A5, foto de
    # celular e PDF convertido, desde que o cartão ocupe a maior parte da imagem.
    regions = [
        img[0:max(1, int(h * 0.30)), max(0, int(w * 0.64)):w],
        img[0:max(1, int(h * 0.38)), max(0, int(w * 0.54)):w],
        img[0:max(1, int(h * 0.46)), max(0, int(w * 0.44)):w],
    ]

    for region in regions:
        for cand in _variantes(region):
            # Primeiro os leitores mais tolerantes, depois OpenCV.
            if cand.ndim == 3:
                texto = _decode_zxing(cand) or _decode_pyzbar(cand)
                if texto:
                    return texto
            texto = _decode_opencv(cand)
            if texto:
                return texto

    return ""


if __name__ == "__main__":
    try:
        result = decode(sys.argv[1]) if len(sys.argv) > 1 else ""
        print(json.dumps({"ok": bool(result), "text": result}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "text": "", "error": str(exc)}, ensure_ascii=False))
        sys.exit(0)

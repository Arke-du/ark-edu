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

    # 1) Tentativa rápida no recorte EXATO onde o QR é impresso nos cartões
    # ARK EDUS. Nos PDFs de scanner, um crop muito amplo fazia o OpenCV gastar
    # vários segundos e, em algumas páginas, nem decodificar um QR perfeitamente
    # legível. O recorte justo + ampliação resolve isso em centésimos de segundo.
    recortes_rapidos = [
        # padrão atual do cartão A4/A5
        (0.715, 0.025, 0.835, 0.165),
        # pequenas variações de enquadramento/scan
        (0.690, 0.015, 0.855, 0.190),
        (0.735, 0.035, 0.825, 0.155),
    ]
    for x1r, y1r, x2r, y2r in recortes_rapidos:
        x1, x2 = int(w * x1r), int(w * x2r)
        y1, y2 = int(h * y1r), int(h * y2r)
        region = img[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
        if region is None or region.size == 0:
            continue
        for escala in (3.0, 4.0, 2.0):
            cand = cv2.resize(
                region, None, fx=escala, fy=escala,
                interpolation=cv2.INTER_CUBIC
            )
            texto = _decode_zxing(cand) or _decode_pyzbar(cand) or _decode_opencv(cand)
            if texto:
                return texto

    # 2) Fallback para fotos fora do enquadramento padrão. Mantemos regiões
    # progressivas, mas só depois da tentativa rápida acima.
    regions = [
        img[0:max(1, int(h * 0.30)), max(0, int(w * 0.64)):w],
        img[0:max(1, int(h * 0.38)), max(0, int(w * 0.54)):w],
        img[0:max(1, int(h * 0.46)), max(0, int(w * 0.44)):w],
    ]

    for region in regions:
        for cand in _variantes(region):
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

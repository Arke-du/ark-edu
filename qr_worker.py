import json
import sys
import cv2


def _resize_limit(img, max_side=1800):
    h, w = img.shape[:2]
    m = max(h, w)
    if m <= max_side:
        return img
    s = max_side / float(m)
    return cv2.resize(img, (max(1, int(w*s)), max(1, int(h*s))), interpolation=cv2.INTER_AREA)


def _decode_one(detector, img):
    try:
        text, pts, _ = detector.detectAndDecode(img)
    except cv2.error:
        return ""
    return (text or "").strip()


def decode(path):
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        return ""
    img = _resize_limit(img)
    h, w = img.shape[:2]
    detector = cv2.QRCodeDetector()

    # O QR dos cartões ARK EDUS fica no topo direito. Trabalhar somente nessa
    # região evita travamentos e picos de RAM do detector em fotos A4 inteiras.
    regions = [
        img[0:max(1, int(h*0.28)), max(0, int(w*0.66)):w],
        img[0:max(1, int(h*0.36)), max(0, int(w*0.55)):w],
    ]

    for region in regions:
        if region is None or region.size == 0:
            continue
        # Pequena margem branca melhora o detector quando o QR está rente ao recorte.
        region = cv2.copyMakeBorder(region, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=(255,255,255))
        for angle in (0, 90, 270, 180):
            if angle == 0:
                rot = region
            elif angle == 90:
                rot = cv2.rotate(region, cv2.ROTATE_90_CLOCKWISE)
            elif angle == 180:
                rot = cv2.rotate(region, cv2.ROTATE_180)
            else:
                rot = cv2.rotate(region, cv2.ROTATE_90_COUNTERCLOCKWISE)

            for scale in (1.0, 1.6):
                cand = rot if scale == 1.0 else cv2.resize(rot, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                text = _decode_one(detector, cand)
                if text:
                    return text
                gray = cv2.cvtColor(cand, cv2.COLOR_BGR2GRAY)
                text = _decode_one(detector, gray)
                if text:
                    return text
                # Só um pré-processamento binário; nada de detectAndDecodeMulti.
                blur = cv2.GaussianBlur(gray, (3,3), 0)
                _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                text = _decode_one(detector, otsu)
                if text:
                    return text
    return ""


if __name__ == "__main__":
    try:
        result = decode(sys.argv[1]) if len(sys.argv) > 1 else ""
        print(json.dumps({"ok": bool(result), "text": result}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "text": "", "error": str(exc)}, ensure_ascii=False))
        sys.exit(0)

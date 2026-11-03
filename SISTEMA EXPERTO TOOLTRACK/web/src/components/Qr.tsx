import QRCode from "qrcode";
import { useEffect, useState } from "react";

/** Código QR dibujado en el navegador (sin servidor). */
export function Qr({ value, size = 220, label }: { value: string; size?: number; label?: string }) {
  const [src, setSrc] = useState<string>("");
  useEffect(() => {
    let alive = true;
    QRCode.toDataURL(value, { width: size * 2, margin: 1, errorCorrectionLevel: "M" })
      .then((url) => alive && setSrc(url))
      .catch(() => alive && setSrc(""));
    return () => {
      alive = false;
    };
  }, [value, size]);
  if (!src) return <div className="qr-empty" style={{ width: size, height: size }} />;
  return <img className="qr" src={src} width={size} height={size} alt={label ?? `QR ${value}`} />;
}

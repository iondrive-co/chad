import { useEffect, useRef, useCallback, useState } from "react";
import jsQR from "jsqr";

// BarcodeDetector is available in Chrome Android and Safari 16.4+ but not yet
// in the standard TypeScript DOM lib.
declare global {
  interface Window {
    BarcodeDetector?: typeof BarcodeDetector;
  }
  class BarcodeDetector {
    constructor(options: { formats: string[] });
    detect(source: HTMLCanvasElement | ImageBitmap): Promise<{ rawValue: string }[]>;
  }
}

interface QRScannerProps {
  onScan: (code: string) => void;
  onCancel: () => void;
}

// Feature-detect BarcodeDetector (Chrome Android, Safari 16.4+)
const hasBarcodeDetector = typeof window.BarcodeDetector !== "undefined";

export function QRScanner({ onScan, onCancel }: QRScannerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const scanningRef = useRef(true);
  const [error, setError] = useState<string | null>(null);

  const stopCamera = useCallback(() => {
    scanningRef.current = false;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  const handleDetected = useCallback(
    (rawValue: string) => {
      // Extract #pair= fragment from a URL, or use the raw value
      let code = rawValue;
      try {
        const url = new URL(rawValue);
        const pairMatch = url.hash.match(/^#pair=(.+)$/);
        if (pairMatch) {
          code = pairMatch[1];
        }
      } catch {
        // Not a URL — use raw value as-is (could be just "subdomain:token")
      }
      stopCamera();
      onScan(code);
    },
    [onScan, stopCamera],
  );

  useEffect(() => {
    let raf = 0;
    let detector: InstanceType<typeof BarcodeDetector> | null = null;

    if (hasBarcodeDetector) {
      detector = new BarcodeDetector({ formats: ["qr_code"] });
    }

    const startCamera = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "environment" },
        });
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play();
          scanLoop();
        }
      } catch {
        setError("Camera access denied. Please allow camera access and try again.");
      }
    };

    const scanLoop = () => {
      if (!scanningRef.current) return;
      const video = videoRef.current;
      const canvas = canvasRef.current;
      if (!video || !canvas || video.readyState < video.HAVE_ENOUGH_DATA) {
        raf = requestAnimationFrame(scanLoop);
        return;
      }

      const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      ctx.drawImage(video, 0, 0);

      if (detector) {
        detector
          .detect(canvas)
          .then((codes) => {
            if (codes.length > 0 && scanningRef.current) {
              handleDetected(codes[0].rawValue);
              return;
            }
            if (scanningRef.current) raf = requestAnimationFrame(scanLoop);
          })
          .catch(() => {
            if (scanningRef.current) raf = requestAnimationFrame(scanLoop);
          });
      } else {
        const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
        const result = jsQR(imageData.data, canvas.width, canvas.height);
        if (result && scanningRef.current) {
          handleDetected(result.data);
          return;
        }
        if (scanningRef.current) raf = requestAnimationFrame(scanLoop);
      }
    };

    scanningRef.current = true;
    startCamera();

    return () => {
      scanningRef.current = false;
      cancelAnimationFrame(raf);
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    };
  }, [handleDetected]);

  const handleCancel = () => {
    stopCamera();
    onCancel();
  };

  if (error) {
    return (
      <div className="qr-scanner">
        <p className="qr-scanner-error">{error}</p>
        <div className="qr-scanner-actions">
          <button onClick={handleCancel}>Back</button>
        </div>
      </div>
    );
  }

  return (
    <div className="qr-scanner">
      <video ref={videoRef} playsInline muted />
      <canvas ref={canvasRef} style={{ display: "none" }} />
      <div className="qr-scanner-actions">
        <button onClick={handleCancel}>Cancel</button>
      </div>
    </div>
  );
}

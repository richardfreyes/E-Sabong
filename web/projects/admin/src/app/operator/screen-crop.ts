/**
 * Broadcast only an area of a shared screen or window.
 *
 * Browsers capture a whole screen, window or tab; the operator's area is cut out here and the
 * cropped picture is what gets encoded and sent. Chrome and Edge crop each captured frame
 * (`VideoFrame.visibleRect` through MediaStreamTrackProcessor/Generator): no copy, and it keeps
 * running while the console tab is in the background (operators usually look at the window they
 * share). Other browsers redraw the area onto a canvas, which needs the console tab visible.
 */

/** An area of the captured picture, as fractions of its width and height (0..1). */
export interface CropRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export const MIN_CROP = 0.05;

/** Pixel rectangle of ``crop`` in a ``width``×``height`` frame: even-aligned (required by video
 * encoders for 4:2:0 frames), at least 32 px, and always inside the frame. */
export function pixelRect(crop: CropRect, width: number, height: number): { x: number; y: number; width: number; height: number } {
  const even = (n: number) => Math.max(0, Math.floor(n / 2) * 2);
  const w = Math.max(32, even(crop.w * width));
  const h = Math.max(32, even(crop.h * height));
  const x = Math.min(even(crop.x * width), even(width - w));
  const y = Math.min(even(crop.y * height), even(height - h));
  return { x, y, width: Math.min(w, even(width)), height: Math.min(h, even(height)) };
}

/** Keep a crop inside the picture and at least MIN_CROP in each direction. */
export function clampCrop(c: CropRect): CropRect {
  const w = Math.min(1, Math.max(MIN_CROP, c.w));
  const h = Math.min(1, Math.max(MIN_CROP, c.h));
  return { x: Math.min(Math.max(0, c.x), 1 - w), y: Math.min(Math.max(0, c.y), 1 - h), w, h };
}

export function isFullFrame(c: CropRect | null): boolean {
  return !c || (c.x <= 0.001 && c.y <= 0.001 && c.w >= 0.999 && c.h >= 0.999);
}

export interface Cropper {
  /** The cropped video track to preview and broadcast. */
  readonly track: MediaStreamTrack;
  /** 'frames' (no copy, background-safe) or 'canvas' (console tab must stay visible). */
  readonly method: 'frames' | 'canvas';
  setCrop(crop: CropRect): void;
  /** Size of the cropped picture in pixels, once known. */
  outputSize(): { width: number; height: number } | null;
  /** Stops the cropped track (the source track is left to its owner). */
  stop(): void;
}

interface TrackProcessorCtor {
  new (init: { track: MediaStreamTrack }): { readable: ReadableStream<VideoFrame> };
}
interface TrackGeneratorCtor {
  new (init: { kind: 'video' }): MediaStreamTrack & { writable: WritableStream<VideoFrame> };
}

export function framesCropSupported(): boolean {
  const g = globalThis as unknown as Record<string, unknown>;
  return typeof g['MediaStreamTrackProcessor'] === 'function' && typeof g['MediaStreamTrackGenerator'] === 'function'
    && typeof g['VideoFrame'] === 'function';
}

export function createCropper(source: MediaStreamTrack, crop: CropRect): Cropper {
  return framesCropSupported() ? new FrameCropper(source, crop) : new CanvasCropper(source, crop);
}

/** Crops each frame in the media pipeline (Chrome, Edge). */
class FrameCropper implements Cropper {
  readonly method = 'frames' as const;
  readonly track: MediaStreamTrack;
  private crop: CropRect;
  private size: { width: number; height: number } | null = null;
  private readonly abort = new AbortController();

  constructor(source: MediaStreamTrack, crop: CropRect) {
    this.crop = crop;
    const g = globalThis as unknown as { MediaStreamTrackProcessor: TrackProcessorCtor; MediaStreamTrackGenerator: TrackGeneratorCtor };
    const processor = new g.MediaStreamTrackProcessor({ track: source });
    const generator = new g.MediaStreamTrackGenerator({ kind: 'video' });
    this.track = generator;
    const transform = new TransformStream<VideoFrame, VideoFrame>({
      transform: (frame, controller) => {
        // Relative to the frame's visible picture: captured frames may be stored padded (coded size
        // larger than the picture) or already offset, and the area refers to what was visible.
        const visible = frame.visibleRect ?? { x: 0, y: 0, width: frame.codedWidth, height: frame.codedHeight };
        const area = pixelRect(this.crop, visible.width, visible.height);
        const rect = { x: visible.x + area.x, y: visible.y + area.y, width: area.width, height: area.height };
        try {
          const cropped = new VideoFrame(frame, { visibleRect: rect, displayWidth: rect.width, displayHeight: rect.height });
          this.size = { width: rect.width, height: rect.height };
          controller.enqueue(cropped);
        } catch {
          controller.enqueue(frame.clone()); // an unsupported crop never freezes the broadcast
        } finally {
          frame.close();
        }
      },
    });
    processor.readable.pipeThrough(transform).pipeTo(generator.writable, { signal: this.abort.signal }).catch(() => undefined);
  }

  setCrop(crop: CropRect): void {
    this.crop = crop;
  }

  outputSize(): { width: number; height: number } | null {
    return this.size;
  }

  stop(): void {
    this.abort.abort();
    this.track.stop();
  }
}

/** Redraws the area onto a canvas (Safari, Firefox). */
class CanvasCropper implements Cropper {
  readonly method = 'canvas' as const;
  readonly track: MediaStreamTrack;
  private crop: CropRect;
  private readonly video = document.createElement('video');
  private readonly canvas = document.createElement('canvas');
  private readonly timer: ReturnType<typeof setInterval>;

  constructor(source: MediaStreamTrack, crop: CropRect) {
    this.crop = crop;
    this.video.muted = true;
    this.video.playsInline = true;
    this.video.srcObject = new MediaStream([source]);
    void this.video.play().catch(() => undefined);
    const settings = source.getSettings();
    const first = pixelRect(crop, settings.width ?? 1280, settings.height ?? 720);
    Object.assign(this.canvas, { width: first.width, height: first.height });
    const ctx = this.canvas.getContext('2d', { alpha: false })!;
    this.timer = setInterval(() => {
      const vw = this.video.videoWidth;
      const vh = this.video.videoHeight;
      if (!vw || !vh) return;
      const r = pixelRect(this.crop, vw, vh);
      if (this.canvas.width !== r.width || this.canvas.height !== r.height) Object.assign(this.canvas, { width: r.width, height: r.height });
      ctx.drawImage(this.video, r.x, r.y, r.width, r.height, 0, 0, r.width, r.height);
    }, 1000 / 30);
    this.track = this.canvas.captureStream(30).getVideoTracks()[0];
  }

  setCrop(crop: CropRect): void {
    this.crop = crop;
  }

  outputSize(): { width: number; height: number } | null {
    return this.canvas.width ? { width: this.canvas.width, height: this.canvas.height } : null;
  }

  stop(): void {
    clearInterval(this.timer);
    this.track.stop();
    this.video.srcObject = null;
  }
}

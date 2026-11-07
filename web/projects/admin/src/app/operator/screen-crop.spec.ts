import { clampCrop, isFullFrame, pixelRect } from './screen-crop';

describe('screen area maths', () => {
  it('maps an area to even, in-frame pixels', () => {
    expect(pixelRect({ x: 0.25, y: 0.25, w: 0.5, h: 0.5 }, 1920, 1080)).toEqual({ x: 480, y: 270, width: 960, height: 540 });
    const odd = pixelRect({ x: 0.1, y: 0.1, w: 0.333, h: 0.333 }, 1366, 768);
    for (const v of Object.values(odd)) expect(v % 2).toBe(0);
    const edge = pixelRect({ x: 0.99, y: 0.99, w: 0.5, h: 0.5 }, 1280, 720);
    expect(edge.x + edge.width).toBeLessThanOrEqual(1280);
    expect(edge.y + edge.height).toBeLessThanOrEqual(720);
  });

  it('keeps areas inside the picture and above the minimum size', () => {
    expect(clampCrop({ x: -1, y: 2, w: 3, h: 0 })).toEqual({ x: 0, y: 0.95, w: 1, h: 0.05 });
  });

  it('treats the whole picture as no area', () => {
    expect(isFullFrame(null)).toBe(true);
    expect(isFullFrame({ x: 0, y: 0, w: 1, h: 1 })).toBe(true);
    expect(isFullFrame({ x: 0, y: 0, w: 0.5, h: 1 })).toBe(false);
  });
});

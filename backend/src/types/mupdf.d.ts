// Manual type declarations for the `mupdf` npm package.
// The package uses package.json "exports" (ESM-style) which TypeScript's
// moduleResolution:"node" (required for NestJS/CommonJS) cannot auto-resolve.
// We declare only the subset used by PdfVisionService.
declare module 'mupdf' {
  export type Matrix = [number, number, number, number, number, number];

  export declare const Matrix: {
    identity: Matrix;
    scale(sx: number, sy: number): Matrix;
  };

  export declare class ColorSpace {
    static readonly DeviceRGB: ColorSpace;
    static readonly DeviceGray: ColorSpace;
  }

  export declare class Pixmap {
    getWidth(): number;
    getHeight(): number;
    asPNG(): Uint8Array;
  }

  export declare class StructuredText {
    asText(): string;
  }

  export declare class Page {
    toStructuredText(options?: string): StructuredText;
    toPixmap(
      matrix: Matrix | number[],
      colorspace: ColorSpace,
      alpha?: boolean,
      antiAlias?: boolean,
    ): Pixmap;
  }

  export declare class Document {
    static openDocument(
      data: Buffer | ArrayBuffer | Uint8Array | string,
      magic?: string,
    ): Document;
    countPages(): number;
    loadPage(index: number): Page;
  }
}

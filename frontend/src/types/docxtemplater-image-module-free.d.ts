declare module 'docxtemplater-image-module-free' {
  type ImageModuleOptions = {
    centered?: boolean
    fileType?: 'docx' | 'pptx'
    getImage: (tagValue: string, tagName: string) => Uint8Array
    getSize: (image: Uint8Array, tagValue: string, tagName: string) => [number, number]
  }

  class ImageModule {
    constructor(options: ImageModuleOptions)
  }

  export default ImageModule
}

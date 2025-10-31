// eslint-disable-next-line @typescript-eslint/no-unused-vars
import {h} from "@stencil/core";

export const OcsWidgetAvatar = () => {
  return <svg width="24" height="24" viewBox="0 0 500 500" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
    <path
      d="M80.1777 149.487C73.7354 160.531 68.6208 172.445 65.0576 185.012C43.6097 196.458 29.0128 219.057 29.0127 245.065C29.0127 270.995 43.5207 293.535 64.8613 305.014C68.3612 317.586 73.409 329.512 79.7881 340.575C34.4248 332.436 2.20245e-05 292.771 0 245.065C0.000198788 197.223 34.6221 157.469 80.1777 149.487ZM419.821 149.487C465.377 157.469 500 197.223 500 245.065C500 292.771 465.575 332.436 420.211 340.575C426.59 329.512 431.638 317.586 435.138 305.014C456.479 293.535 470.987 270.995 470.987 245.065C470.987 219.056 456.39 196.458 434.941 185.012C431.378 172.445 426.264 160.532 419.821 149.487ZM259.868 16.4473C304.099 16.4473 341.297 46.5498 352.097 87.3848C340.566 81.9422 328.254 77.8819 315.375 75.4209C303.51 57.3742 283.08 45.46 259.868 45.46H253.289C230.975 45.4601 211.232 56.4698 199.197 73.3535C186.6 74.535 174.442 77.2268 162.906 81.248C175.656 43.5694 211.305 16.4474 253.289 16.4473H259.868Z"
    />
    <path
      d="M286.185 72.6685C371.571 72.6686 440.789 141.888 440.789 227.274V263.458C440.789 348.844 371.57 418.064 286.185 418.064H213.815C128.43 418.064 59.2111 348.844 59.2109 263.458V227.274C59.211 141.888 128.43 72.6686 213.815 72.6685H286.185ZM213.815 105.263C142.963 105.263 85.5265 162.7 85.5264 233.552V256.579C85.5264 327.431 142.963 384.868 213.815 384.869H286.185C357.037 384.868 414.474 327.431 414.474 256.579V233.552C414.473 162.7 357.037 105.263 286.185 105.263H213.815Z"
    />
    <rect x="289.475" y="184.808" width="61.9019" height="115.73" rx="30.951" />
    <rect x="161.184" y="184.808" width="61.9019" height="115.73" rx="30.951" />
    <path d="M325.658 483.553V414.58V401.316H148.027L325.658 483.553Z" />
  </svg>;
}

export const XMarkIcon = () => {
  return <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5"
              stroke="currentColor">
    <path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12"/>
  </svg>;
}

export const GripDotsVerticalIcon = () => {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      fill="currentColor"
      viewBox="0 0 24 24"
    >
      {/* Left column of dots */}
      <circle cx="8" cy="6" r="1.5"/>
      <circle cx="8" cy="12" r="1.5"/>
      <circle cx="8" cy="18" r="1.5"/>

      {/* Right column of dots */}
      <circle cx="16" cy="6" r="1.5"/>
      <circle cx="16" cy="12" r="1.5"/>
      <circle cx="16" cy="18" r="1.5"/>
    </svg>
  );
};

export const PlusWithCircleIcon = () => {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
      <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v6m3-3H9m12 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"/>
    </svg>
  )
}

export const ArrowsPointingOutIcon = () => {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
      <path stroke-linecap="round" stroke-linejoin="round"
            d="M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M3.75 20.25v-4.5m0 4.5h4.5m-4.5 0L9 15M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25h-4.5m4.5 0v-4.5m0 4.5L15 15"/>
    </svg>
  )
}

export const ArrowsPointingInIcon = () => {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
      <path stroke-linecap="round" stroke-linejoin="round"
            d="M9 9V4.5M9 9H4.5M9 9 3.75 3.75M15 9h4.5M15 9V4.5M15 9l5.25-5.25M9 15v4.5M9 15H4.5M9 15l-5.25 5.25M15 15h4.5M15 15v4.5m0-4.5 5.25 5.25"/>
    </svg>
  )
}

export const PaperClipIcon = () => {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
      <path stroke-linecap="round" stroke-linejoin="round"
            d="m18.375 12.739-7.693 7.693a4.5 4.5 0 0 1-6.364-6.364l10.94-10.94A3 3 0 1 1 19.5 7.372L8.552 18.32m.009-.01-.01.01m5.699-9.941-7.81 7.81a1.5 1.5 0 0 0 2.112 2.13"/>
    </svg>
  )
}

export const CheckDocumentIcon = () => {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
      <path stroke-linecap="round" stroke-linejoin="round"
            d="M10.125 2.25h-4.5c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125v-9M10.125 2.25h.375a9 9 0 0 1 9 9v.375M10.125 2.25A3.375 3.375 0 0 1 13.5 5.625v1.5c0 .621.504 1.125 1.125 1.125h1.5a3.375 3.375 0 0 1 3.375 3.375M9 15l2.25 2.25L15 12"/>
    </svg>

  )
}
export const XIcon = () => {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
      <path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12"/>
    </svg>
  )
}

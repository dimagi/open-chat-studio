import {h} from "@stencil/core";

export const XMarkIcon = () => {
  return <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5"
              stroke="currentColor" class="size-6">
    <path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12"/>
  </svg>;
}

export const GripDotsVerticalIcon = () => {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      fill="currentColor"
      viewBox="0 0 24 24"
      class="size-6"
    >
      {/* Left column of dots */}
      <circle cx="8" cy="6" r="1.5" />
      <circle cx="8" cy="12" r="1.5" />
      <circle cx="8" cy="18" r="1.5" />

      {/* Right column of dots */}
      <circle cx="16" cy="6" r="1.5" />
      <circle cx="16" cy="12" r="1.5" />
      <circle cx="16" cy="18" r="1.5" />
    </svg>
  );
};

export const PencilSquare = () => {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"
         class="size-6">
      <path stroke-linecap="round" stroke-linejoin="round"
            d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10"/>
    </svg>
  )
}

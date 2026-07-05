// "Send Image" — lets the user attach a picture (browsed, dragged, or pasted) to their next
// message, alongside the companion's own take_screenshot tool. attachedImageDataUrl is the
// single shared piece of state; chat-stream.js and voice.js read/clear it when a message sends.

const MAX_IMAGE_BYTES = 20 * 1024 * 1024; // 20MB — generous; the backend downscales for the LLM anyway.

function clearAttachedImage() {
  attachedImageDataUrl = null;
  attachedImageThumb.src = "";
  attachedImageIndicator.hidden = true;
}

function setAttachedImage(dataUrl) {
  attachedImageDataUrl = dataUrl;
  attachedImageThumb.src = dataUrl;
  attachedImageIndicator.hidden = false;
}

function handleImageFile(file) {
  if (!file) return;
  if (!file.type.startsWith("image/")) {
    showToast("send-image-error", { title: "Not an image", body: "That file doesn't look like an image.", duration: 4000 });
    return;
  }
  if (file.size > MAX_IMAGE_BYTES) {
    showToast("send-image-error", { title: "Image too large", body: "Please choose an image under 20MB.", duration: 4000 });
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    setAttachedImage(reader.result);
    closeModal(sendImageModal);
  };
  reader.readAsDataURL(file);
}

sendImageBtn.addEventListener("click", () => openModal(sendImageModal));
attachedImageRemoveBtn.addEventListener("click", clearAttachedImage);

imageDropzone.addEventListener("click", () => imageFileInput.click());
imageDropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    imageFileInput.click();
  }
});

imageFileInput.addEventListener("change", () => {
  handleImageFile(imageFileInput.files[0]);
  imageFileInput.value = "";
});

imageDropzone.addEventListener("dragover", (event) => {
  event.preventDefault();
  imageDropzone.classList.add("drag-over");
});
imageDropzone.addEventListener("dragleave", () => imageDropzone.classList.remove("drag-over"));
imageDropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  imageDropzone.classList.remove("drag-over");
  handleImageFile(event.dataTransfer.files[0]);
});

// Paste anywhere while the modal is open — no need to focus the dropzone first.
document.addEventListener("paste", (event) => {
  if (sendImageModal.hidden) return;
  const item = Array.from(event.clipboardData?.items || []).find((i) => i.type.startsWith("image/"));
  if (item) handleImageFile(item.getAsFile());
});

const megabyte_in_bytes = 1048576;
const byte_limit = megabyte_in_bytes * 512;
const code_interpreter_max_files = 20;

export default function fileUploads(maxCharLimit = null) {
  return {
    message: "",
    maxCharLimit: maxCharLimit,
    code_interpreter_files: [],
    file_search_files: [],
    ocs_attachments: [],
    total_upload_size_bytes: 0,
    disableCodeInterpreterUpload: false,
    get messageTooLong() {
      return this.maxCharLimit !== null && this.message.length > this.maxCharLimit;
    },
    init() {
      let fieldsToWatch = ['message'];
      if (this.$refs.fileSearch) {
        fieldsToWatch.push('file_search_files');
      }
      if (this.$refs.codeInterpreter) {
        fieldsToWatch.push('code_interpreter_files');
      }
      if (this.$refs.experimentUpload) {
        fieldsToWatch.push('ocs_attachments');
      }
      this.$watchMany(fieldsToWatch, (...args) => {
        const submitButton = document.getElementById('message-submit');
        const hasContent = args.some(Boolean);
        if (hasContent && !this.messageTooLong) {
          submitButton.removeAttribute('disabled');
        } else {
          submitButton.setAttribute('disabled', 'disabled');
        }
      });
    },
    $watchMany(fields, handler) {
      fields.forEach((field, idx) => {
        this.$watch(field, (val) => {
          const update = fields.map((f) => f === field ? val : this[f]);
          handler(...update);
        });
      });
    },
    handleFileChange(event, targetArray) {
      const files = Array.from(event.target.files);

      let new_upload_bytes = 0
      for (let i = 0; i < files.length; i++) {
        new_upload_bytes = new_upload_bytes + files[i].size
      }

      if (this.total_upload_size_bytes + new_upload_bytes > byte_limit) {
        let current_files_mb = this.total_upload_size_bytes / megabyte_in_bytes
        let message = "Unable to add new file(s). The maximum upload capacity is 512MB. Current size is " + current_files_mb + "MB";
        alert(message);
        return;
      }

      if (targetArray == "code_interpreter_files") {
        let curr_file_count = this[targetArray].length;
        let new_file_count = curr_file_count + files.length;

        if (new_file_count == code_interpreter_max_files) {
          this.disableCodeInterpreterUpload = true;
        } else if (new_file_count > code_interpreter_max_files) {
          alert("You cannot add more then " + code_interpreter_max_files + " files to code interpreter");
          return;
        }
      }

      this.total_upload_size_bytes = this.total_upload_size_bytes + new_upload_bytes
      this[targetArray].push(...files);
      const dataTransfer = new DataTransfer();
      this[targetArray].forEach(file => dataTransfer.items.add(file));
      event.target.files = dataTransfer.files;
    },
    removeFile(index, targetArray) {
      this[targetArray].splice(index, 1);
      const dataTransfer = new DataTransfer();
      this.total_upload_size_bytes = 0;

      this[targetArray].forEach(file => {
        dataTransfer.items.add(file)
        this.total_upload_size_bytes = this.total_upload_size_bytes + file.size
      });
      if (targetArray === 'code_interpreter_files') {
        this.$refs.codeInterpreter.files = dataTransfer.files;
        if (this.$refs.codeInterpreter.files.length < code_interpreter_max_files) {
          this.disableCodeInterpreterUpload = false;
        }

      } else if (targetArray === 'file_search_files') {
        this.$refs.fileSearch.files = dataTransfer.files;
      } else if (targetArray === 'ocs_attachments') {
        this.$refs.experimentUpload.files = dataTransfer.files;
      }
    }
  };
}
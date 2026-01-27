export interface UploadedFile {
  id: number;
  name: string;
  size: number;
  content_type: string;
}

export interface SelectedFile {
  file: File;
  uploaded?: UploadedFile;
  error?: string;
}

export interface AttachmentValidationConfig {
  supportedExtensions: string[];
  maxFileSizeMb: number;
  maxTotalSizeMb: number;
}

export interface UploadContext {
  apiBaseUrl: string;
  sessionId: string;
  participantId: string;
  participantName?: string;
}

export interface UploadResult {
  selectedFiles: SelectedFile[];
  uploadedIds: number[];
  errorMessage?: string;
}

export class FileAttachmentManager {
  private readonly supportedExtensions: string[];
  private readonly maxFileSizeMb: number;
  private readonly maxTotalSizeMb: number;

  constructor(config: AttachmentValidationConfig) {
    this.supportedExtensions = config.supportedExtensions;
    this.maxFileSizeMb = config.maxFileSizeMb;
    this.maxTotalSizeMb = config.maxTotalSizeMb;
  }

  addFiles(existingFiles: SelectedFile[], files: FileList | File[]): SelectedFile[] {
    const newSelected: SelectedFile[] = [];
    const fileArray = Array.from(files instanceof FileList ? Array.from(files) : files);
    let totalSize = existingFiles.reduce((sum, f) => sum + f.file.size, 0);

    for (const file of fileArray) {
      const extension = this.getFileExtension(file.name);
      const contentType = file.type.split("/")[0];
      if (contentType != "text" && !this.supportedExtensions.includes(extension)) {
        newSelected.push({ file, error: `File type ${extension} not supported` });
        continue;
      }

      const fileSizeMb = this.bytesToMb(file.size);
      if (fileSizeMb > this.maxFileSizeMb) {
        newSelected.push({ file, error: `File exceeds ${this.maxFileSizeMb}MB limit` });
        continue;
      }

      totalSize += file.size;
      const totalSizeMb = this.bytesToMb(totalSize);
      if (totalSizeMb > this.maxTotalSizeMb) {
        newSelected.push({ file, error: `Total size exceeds ${this.maxTotalSizeMb}MB limit` });
        continue;
      }

      newSelected.push({ file });
    }

    return [...existingFiles, ...newSelected];
  }

  removeFile(existingFiles: SelectedFile[], index: number): SelectedFile[] {
    return existingFiles.filter((_, i) => i !== index);
  }

  markPendingFilesWithError(existingFiles: SelectedFile[], errorMessage: string): SelectedFile[] {
    return existingFiles.map(file => {
      if (!file.error && !file.uploaded) {
        return { ...file, error: errorMessage };
      }
      return file;
    });
  }

  async uploadPendingFiles(
    existingFiles: SelectedFile[],
    context: UploadContext
  ): Promise<UploadResult> {
    if (existingFiles.length === 0) {
      return { selectedFiles: existingFiles, uploadedIds: [] };
    }

    const uploadCandidates = existingFiles.filter(file => !file.error && !file.uploaded);
    const uploadedIds: number[] = existingFiles
      .filter(file => file.uploaded)
      .map(file => file.uploaded!.id);

    if (uploadCandidates.length === 0) {
      return { selectedFiles: existingFiles, uploadedIds };
    }

    const formData = new FormData();
    for (const file of uploadCandidates) {
      formData.append('files', file.file);
    }
    formData.append('participant_remote_id', context.participantId);
    if (context.participantName) {
      formData.append('participant_name', context.participantName);
    }

    try {
      const response = await fetch(`${context.apiBaseUrl}/api/chat/${context.sessionId}/upload/`, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const errorData = await this.safeJson(response);
        const errorMessage =
          (errorData && typeof errorData === 'object' && 'error' in errorData && (errorData as { error?: string }).error) ||
          'Failed to upload files';
        return {
          selectedFiles: this.markPendingFilesWithError(existingFiles, errorMessage),
          uploadedIds,
          errorMessage,
        };
      }

      const data = await this.safeJson(response);
      if (!data || typeof data !== 'object' || !Array.isArray((data as { files?: unknown }).files)) {
        const errorMessage = 'Unexpected upload response shape';
        return {
          selectedFiles: this.markPendingFilesWithError(existingFiles, errorMessage),
          uploadedIds,
          errorMessage,
        };
      }

      const uploadedFiles = (data as { files: UploadedFile[] }).files;
      let index = 0;
      const updatedSelected = existingFiles.map(file => {
        if (!file.error && !file.uploaded) {
          const uploaded = uploadedFiles[index];
          index += 1;
          return { ...file, uploaded };
        }
        return file;
      });
      uploadedIds.push(...uploadedFiles.map(file => file.id));

      return { selectedFiles: updatedSelected, uploadedIds };
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Failed to upload files';
      return {
        selectedFiles: this.markPendingFilesWithError(existingFiles, errorMessage),
        uploadedIds,
        errorMessage,
      };
    }
  }

  private bytesToMb(bytes: number): number {
    return bytes / (1024 * 1024);
  }

  private getFileExtension(filename: string): string {
    const parts = filename.split('.');
    const ext = parts.pop();
    return ext ? `.${ext.toLowerCase()}` : '';
  }

  private async safeJson(response: Response): Promise<unknown> {
    try {
      return await response.json();
    } catch {
      return undefined;
    }
  }
}

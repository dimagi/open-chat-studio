import logging
import sys
import traceback

logger = logging.getLogger("ocs.pipelines")


def get_code_error_message(filename: str, code: str) -> str:
    exc_type, exc_value, exc_traceback = sys.exc_info()
    error_message = f"Error: {exc_value!r}"

    try:
        tb_list = traceback.extract_tb(exc_traceback)
        user_frames = [frame for frame in tb_list if filename in frame.filename]

        if user_frames:
            error_frame = user_frames[-1]  # Last frame in user code
            line_number = error_frame.lineno

            source_lines = code.splitlines()
            if 1 <= line_number <= len(source_lines):
                # Show context (lines around the error)
                start = max(0, line_number - 3)
                end = min(len(source_lines), line_number + 2)

                error_message += "\nContext:"
                for i in range(start, end):
                    marker = ">>>" if i + 1 == line_number else "   "
                    error_message += f"\n{marker} {i + 1:3d}: {source_lines[i]}"
    except Exception:
        logger.exception("Error while getting code error message")

    return error_message

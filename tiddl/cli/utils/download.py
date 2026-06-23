from logging import getLogger
from pathlib import Path
from tiddl.core.api.models import TrackQuality

log = getLogger(__name__)


def get_existing_track_filename(
    track_quality: TrackQuality, download_quality: TrackQuality, file_name: Path
) -> Path:
    """
    Predict track extension.
    
    Only LOSSLESS (CD-quality FLAC via BTS manifest) produces .flac files.
    HI_RES_LOSSLESS is delivered as AAC-in-MP4 via DASH, so it uses .m4a.
    """

    if download_quality == "LOSSLESS" and track_quality == "LOSSLESS":
        extension = ".flac"
    else:
        extension = ".m4a"

    full_file_name = file_name.with_suffix(extension)

    log.debug(f"{track_quality=}, {download_quality=}, {file_name=}, {full_file_name=}")

    return full_file_name

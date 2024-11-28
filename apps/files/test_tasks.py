import pytest
from django.utils import timezone

from apps.files.models import File
from apps.files.tasks import clean_up_expired_files
from apps.utils.factories.files import FileFactory


@pytest.mark.django_db()
def test_clean_up_expired_files():
    expired_file = FileFactory(expiry_date=timezone.now() - timezone.timedelta(days=1, minutes=1))
    non_expired_file = FileFactory(expiry_date=timezone.now() + timezone.timedelta(days=1, minutes=1))
    clean_up_expired_files()

    with pytest.raises(File.DoesNotExist):
        expired_file.refresh_from_db()

    # This will throw an exception if the file does not exist
    non_expired_file.refresh_from_db()

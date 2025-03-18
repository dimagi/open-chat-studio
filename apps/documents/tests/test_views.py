import pytest

from apps.documents.models import Repository, RepositoryType
from apps.documents.views import _upsert_collection_membership
from apps.files.models import File


@pytest.mark.django_db()
def test_upsert_collection_membership(team):
    file = File.objects.create(team=team)

    repo1 = Repository.objects.create(type=RepositoryType.COLLECTION, team=team, name="repo1")
    repo2 = Repository.objects.create(type=RepositoryType.COLLECTION, team=team, name="repo2")
    repo3 = Repository.objects.create(type=RepositoryType.COLLECTION, team=team, name="repo3")

    repo1.files.add(file)
    repo2.files.add(file)

    repo_set = file.repository_set.filter(type=RepositoryType.COLLECTION)
    assert repo1 in repo_set
    assert repo2 in repo_set
    assert repo3 not in repo_set
    # This should remove the file from repo1 and add it to repo3 while leaving repo2 as-is
    _upsert_collection_membership(file, ["repo2", "repo3"])

    repo_set = file.repository_set.filter(type=RepositoryType.COLLECTION)
    assert repo1 not in repo_set
    assert repo2 in repo_set
    assert repo3 in repo_set

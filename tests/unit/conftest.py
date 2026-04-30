"""Unit test fixtures."""

import pytest

from curator.models import (
    Basics,
    CertificateEntry,
    EducationEntry,
    InterestData,
    LanguageEntry,
    PortfolioData,
    ProjectEntry,
    PublicationEntry,
    ServiceEntry,
    SkillEntry,
    VolunteerEntry,
    WorkEntry,
)


@pytest.fixture
def work_entry_dict() -> dict[str, object]:
    """Minimal valid work entry matching YAML structure."""
    return {
        "id": "acme-senior-engineer",
        "name": "Acme Corp",
        "position": "Senior Engineer",
        "startDate": "2023-06",
        "endDate": "",
        "location": "Remote",
        "summary": "Led platform engineering.",
        "description": "Platform, Infrastructure",
        "highlights": [
            {
                "id": "acme-deployed-k8s",
                "text": "Deployed Kubernetes cluster serving 10k RPS.",
                "tags": ["infrastructure", "kubernetes"],
                "resume_variants": ["general", "devops"],
                "technologies": ["Kubernetes", "AWS"],
            },
        ],
        "tags": ["infrastructure", "platform"],
        "resume_variants": ["general", "devops"],
        "technologies": ["Kubernetes", "AWS", "Terraform"],
    }


@pytest.fixture
def basics_dict() -> dict[str, object]:
    """Minimal valid basics entry."""
    return {
        "name": "Jane Doe",
        "label": "Software Engineer",
        "email": "jane@example.com",
        "summary": "Experienced engineer.",
        "location": {
            "countryCode": "US",
            "region": "CA",
        },
        "profiles": [
            {
                "network": "GitHub",
                "username": "janedoe",
                "url": "https://github.com/janedoe",
            },
        ],
    }


@pytest.fixture
def education_entry_dict() -> dict[str, object]:
    """Minimal valid education entry matching YAML structure."""
    return {
        "id": "umw-bs-cs",
        "institution": "University of Mary Washington",
        "area": "Computer Science",
        "studyType": "Bachelor of Science",
        "startDate": "2014",
        "endDate": "2018",
    }


@pytest.fixture
def skill_entry_dict() -> dict[str, object]:
    """Minimal valid skill entry."""
    return {"id": "cloud-aws", "name": "AWS", "level": "Advanced", "keywords": ["EKS"]}


@pytest.fixture
def certificate_entry_dict() -> dict[str, object]:
    """Minimal valid certificate entry."""
    return {
        "id": "cka",
        "name": "CKA",
        "date": "2023",
        "type": "professional",
        "issuer": "CNCF",
    }


@pytest.fixture
def project_entry_dict() -> dict[str, object]:
    """Minimal valid project entry."""
    return {"id": "my-project", "name": "My Project", "description": "A project."}


@pytest.fixture
def volunteer_entry_dict() -> dict[str, object]:
    """Minimal valid volunteer entry."""
    return {"id": "spca", "organization": "SPCA", "position": "Volunteer"}


@pytest.fixture
def publication_entry_dict() -> dict[str, object]:
    """Minimal valid publication entry."""
    return {
        "id": "tech-talk",
        "name": "Talk",
        "type": "presentation",
        "releaseDate": "2025",
    }


@pytest.fixture
def language_entry_dict() -> dict[str, object]:
    """Minimal valid language entry."""
    return {"id": "english", "language": "English", "fluency": "Native"}


@pytest.fixture
def interest_data_dict() -> dict[str, object]:
    """Minimal valid interest data (single object, not array)."""
    return {
        "hobbies": [
            {
                "name": "Learning",
                "description": "Always learning.",
                "keywords": ["tech"],
            },
        ],
        "fun_facts": ["Interesting fact."],
    }


@pytest.fixture
def service_entry_dict() -> dict[str, object]:
    """Minimal valid service entry."""
    return {
        "id": "cloud-infra",
        "name": "Cloud Infrastructure",
        "slug": "cloud-infrastructure",
    }


@pytest.fixture
def portfolio_data(
    basics_dict: dict[str, object],
    work_entry_dict: dict[str, object],
    skill_entry_dict: dict[str, object],
    certificate_entry_dict: dict[str, object],
    project_entry_dict: dict[str, object],
    education_entry_dict: dict[str, object],
    volunteer_entry_dict: dict[str, object],
    publication_entry_dict: dict[str, object],
    language_entry_dict: dict[str, object],
    interest_data_dict: dict[str, object],
    service_entry_dict: dict[str, object],
) -> PortfolioData:
    """Build a complete PortfolioData from existing dict fixtures."""
    return PortfolioData(
        basics=Basics.model_validate(basics_dict),
        work=[WorkEntry.model_validate(work_entry_dict)],
        education=[EducationEntry.model_validate(education_entry_dict)],
        skills=[SkillEntry.model_validate(skill_entry_dict)],
        certificates=[CertificateEntry.model_validate(certificate_entry_dict)],
        projects=[ProjectEntry.model_validate(project_entry_dict)],
        volunteer=[VolunteerEntry.model_validate(volunteer_entry_dict)],
        publications=[PublicationEntry.model_validate(publication_entry_dict)],
        languages=[LanguageEntry.model_validate(language_entry_dict)],
        interests=InterestData.model_validate(interest_data_dict),
        services=[ServiceEntry.model_validate(service_entry_dict)],
    )


@pytest.fixture
def resume_curation_dict() -> dict[str, object]:
    """Minimal valid ResumeCuration structured output.

    Note: IDs here (e.g., ``"infra-toolkit"``) intentionally do NOT match
    the ``portfolio_data`` fixture IDs (e.g., ``"my-project"``). This
    fixture is used for model-level tests that validate schema structure,
    not for client-level tests that perform ID validation. See
    ``test_client.py::valid_curation_dict`` for portfolio-matching IDs.
    """
    return {
        "summary": "Experienced SRE and founder of Perts Foundry LLC "
        "(a consulting company) with focus on reliability.",
        "suggested_label": "Senior DevOps Engineer",
        "company_slug": "acme-corp",
        "work_highlights": [
            {
                "work_id": "acme-senior-engineer",
                "highlight_ids": ["acme-deployed-k8s"],
            },
        ],
        "skills": [{"skill_id": "cloud-aws", "keywords": ["EKS"]}],
        "projects": ["infra-toolkit"],
    }

"""Tests for curator.models."""

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from curator.models import (
    Basics,
    CertificateEntry,
    EducationEntry,
    Hobby,
    InterestData,
    LanguageEntry,
    Location,
    PortfolioData,
    ProjectEntry,
    PublicationEntry,
    ResumeCuration,
    ServiceEntry,
    SkillEntry,
    SkillRanking,
    TaggedHighlight,
    VolunteerEntry,
    WorkEntry,
    WorkHighlightRanking,
)

# ---------------------------------------------------------------------------
# Portfolio data models
# ---------------------------------------------------------------------------


class TestTaggedHighlight:
    def test_valid(self) -> None:
        h = TaggedHighlight(id="deploy-k8s", text="Deployed cluster.")
        assert h.id == "deploy-k8s"
        assert h.text == "Deployed cluster."
        assert h.tags == []
        assert h.resume_variants == []
        assert h.technologies == []

    def test_full_fields(self) -> None:
        h = TaggedHighlight(
            id="deploy-k8s",
            text="Deployed cluster.",
            tags=["infra"],
            resume_variants=["general", "devops"],
            technologies=["Kubernetes"],
        )
        assert h.tags == ["infra"]
        assert h.resume_variants == ["general", "devops"]
        assert h.technologies == ["Kubernetes"]

    @pytest.mark.parametrize("bad_id", ["Invalid", "has space", "-leading"])
    def test_invalid_id_rejected(self, bad_id: str) -> None:
        with pytest.raises(ValidationError, match="id"):
            TaggedHighlight(id=bad_id, text="Some text")

    def test_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            TaggedHighlight(id="valid-id")  # type: ignore[call-arg]

    def test_frozen(self) -> None:
        h = TaggedHighlight(id="test-id", text="text")
        with pytest.raises(ValidationError):
            h.text = "changed"

    def test_invalid_resume_variant_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TaggedHighlight(
                id="test-id",
                text="text",
                resume_variants=["invalid"],  # type: ignore[list-item]
            )


class TestWorkEntry:
    def test_valid_from_yaml_dict(self, work_entry_dict: dict[str, object]) -> None:
        entry = WorkEntry.model_validate(work_entry_dict)
        assert entry.id == "acme-senior-engineer"
        assert entry.name == "Acme Corp"
        assert entry.position == "Senior Engineer"
        assert entry.start_date == "2023-06"
        assert entry.end_date is None  # empty string normalized
        assert entry.location == "Remote"
        assert entry.summary == "Led platform engineering."
        assert len(entry.highlights) == 1
        assert entry.highlights[0].id == "acme-deployed-k8s"

    def test_camel_case_alias(self) -> None:
        entry = WorkEntry.model_validate(
            {
                "id": "test-co",
                "name": "Test",
                "position": "Dev",
                "startDate": "2024-01",
            }
        )
        assert entry.start_date == "2024-01"

    def test_populate_by_name(self) -> None:
        entry = WorkEntry(
            id="test-co",
            name="Test",
            position="Dev",
            start_date="2024-01",
        )
        assert entry.start_date == "2024-01"

    def test_empty_end_date_normalized(self) -> None:
        entry = WorkEntry.model_validate(
            {
                "id": "test-co",
                "name": "Test",
                "position": "Dev",
                "startDate": "2024",
                "endDate": "",
            }
        )
        assert entry.end_date is None

    def test_none_end_date_passthrough(self) -> None:
        entry = WorkEntry(
            id="test-co",
            name="Test",
            position="Dev",
            start_date="2024",
            end_date=None,
        )
        assert entry.end_date is None

    def test_valid_end_date_preserved(self) -> None:
        entry = WorkEntry.model_validate(
            {
                "id": "test-co",
                "name": "Test",
                "position": "Dev",
                "startDate": "2024",
                "endDate": "2025-01",
            }
        )
        assert entry.end_date == "2025-01"

    def test_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            WorkEntry.model_validate({"id": "test", "name": "Test"})

    def test_extra_fields_ignored(self) -> None:
        entry = WorkEntry.model_validate(
            {
                "id": "test-co",
                "name": "Test",
                "position": "Dev",
                "startDate": "2024",
                "unknown_field": "dropped",
            }
        )
        assert not hasattr(entry, "unknown_field")

    def test_frozen(self) -> None:
        entry = WorkEntry(
            id="test-co",
            name="Test",
            position="Dev",
            start_date="2024",
        )
        with pytest.raises(ValidationError):
            entry.name = "Changed"


class TestBasics:
    def test_valid_from_dict(self, basics_dict: dict[str, object]) -> None:
        basics = Basics.model_validate(basics_dict)
        assert basics.name == "Jane Doe"
        assert basics.label == "Software Engineer"
        assert basics.email == "jane@example.com"
        assert basics.summary == "Experienced engineer."
        assert basics.location is not None
        assert basics.location.country_code == "US"
        assert basics.location.region == "CA"
        assert len(basics.profiles) == 1
        assert basics.profiles[0].network == "GitHub"

    def test_minimal(self) -> None:
        basics = Basics(name="Min")
        assert basics.name == "Min"
        assert basics.label is None
        assert basics.location is None
        assert basics.profiles == []

    def test_missing_name_raises(self) -> None:
        with pytest.raises(ValidationError, match="name"):
            Basics.model_validate({})


class TestLocation:
    def test_camel_case_aliases(self) -> None:
        loc = Location.model_validate({"postalCode": "22030", "countryCode": "US"})
        assert loc.postal_code == "22030"
        assert loc.country_code == "US"

    def test_populate_by_name(self) -> None:
        loc = Location(postal_code="22030", country_code="US")
        assert loc.postal_code == "22030"


class TestEducationEntry:
    def test_valid(self) -> None:
        entry = EducationEntry.model_validate(
            {
                "id": "umw-bs-cs",
                "institution": "University of Mary Washington",
                "area": "Computer Science",
                "studyType": "Bachelor of Science",
                "startDate": "2014",
                "endDate": "2018",
                "score": "3.82/4.0",
                "honors": "Summa Cum Laude",
                "resume_variants": ["general"],
            }
        )
        assert entry.study_type == "Bachelor of Science"
        assert entry.start_date == "2014"
        assert entry.end_date == "2018"
        assert entry.honors == "Summa Cum Laude"


class TestSkillEntry:
    def test_valid(self) -> None:
        entry = SkillEntry(
            id="cloud-aws",
            name="AWS",
            level="Advanced",
            keywords=["EKS", "ECS", "Lambda"],
            resume_variants=["general", "devops"],
        )
        assert entry.name == "AWS"
        assert len(entry.keywords) == 3


class TestCertificateEntry:
    def test_valid(self) -> None:
        entry = CertificateEntry(
            id="terraform-associate",
            name="Terraform Associate",
            date="2023",
            type="professional",
            issuer="HashiCorp",
        )
        assert entry.type == "professional"
        assert entry.issuer == "HashiCorp"

    def test_invalid_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CertificateEntry(
                id="cert",
                name="Cert",
                date="2023",
                type="invalid",  # type: ignore[arg-type]
            )


class TestProjectEntry:
    def test_valid_with_highlights(self) -> None:
        entry = ProjectEntry.model_validate(
            {
                "id": "my-project",
                "name": "My Project",
                "description": "A cool project.",
                "startDate": "2025",
                "roles": ["Creator"],
                "highlights": [
                    {"id": "proj-feat", "text": "Built feature X."},
                ],
                "technologies": ["Python", "Docker"],
            }
        )
        assert len(entry.highlights) == 1
        assert entry.roles == ["Creator"]
        assert entry.start_date == "2025"


class TestVolunteerEntry:
    def test_valid(self) -> None:
        entry = VolunteerEntry.model_validate(
            {
                "id": "spca-volunteer",
                "organization": "SPCA",
                "position": "Volunteer",
                "startDate": "2022",
                "endDate": "",
            }
        )
        assert entry.organization == "SPCA"
        assert entry.end_date is None


class TestPublicationEntry:
    def test_valid(self) -> None:
        entry = PublicationEntry.model_validate(
            {
                "id": "tech-talk",
                "name": "Using Cursor",
                "type": "presentation",
                "publisher": "Engineering Team",
                "releaseDate": "2025-09",
            }
        )
        assert entry.type == "presentation"
        assert entry.release_date == "2025-09"

    def test_invalid_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PublicationEntry(
                id="pub",
                name="Pub",
                type="invalid",  # type: ignore[arg-type]
            )


class TestLanguageEntry:
    def test_valid(self) -> None:
        entry = LanguageEntry(id="english", language="English", fluency="Native")
        assert entry.language == "English"
        assert entry.fluency == "Native"

    def test_minimal(self) -> None:
        entry = LanguageEntry(id="spanish", language="Spanish")
        assert entry.fluency is None

    def test_missing_language_rejected(self) -> None:
        with pytest.raises(ValidationError, match="language"):
            LanguageEntry(id="lang-1")  # type: ignore[call-arg]


class TestHobby:
    def test_valid(self) -> None:
        hobby = Hobby(
            name="Learning",
            description="Always learning new things.",
            keywords=["tech", "books"],
        )
        assert hobby.name == "Learning"
        assert hobby.keywords == ["tech", "books"]

    def test_minimal(self) -> None:
        hobby = Hobby(name="Hiking")
        assert hobby.description is None
        assert hobby.keywords == []

    def test_frozen(self) -> None:
        hobby = Hobby(name="Hiking")
        with pytest.raises(ValidationError):
            hobby.name = "Running"


class TestInterestData:
    def test_valid(self, interest_data_dict: dict[str, object]) -> None:
        data = InterestData.model_validate(interest_data_dict)
        assert len(data.hobbies) == 1
        assert data.hobbies[0].name == "Learning"
        assert data.fun_facts == ["Interesting fact."]

    def test_empty(self) -> None:
        data = InterestData()
        assert data.hobbies == []
        assert data.fun_facts == []

    def test_frozen(self) -> None:
        data = InterestData()
        with pytest.raises(ValidationError):
            data.fun_facts = ["new"]


class TestServiceEntry:
    def test_valid(self) -> None:
        entry = ServiceEntry(
            id="cloud-infra",
            name="Cloud Infrastructure",
            slug="cloud-infrastructure",
            summary="AWS/GCP cloud setup.",
            weight=1,
        )
        assert entry.name == "Cloud Infrastructure"
        assert entry.slug == "cloud-infrastructure"
        assert entry.weight == 1

    def test_minimal(self) -> None:
        entry = ServiceEntry(id="svc-1", name="Service", slug="service")
        assert entry.summary is None
        assert entry.weight is None
        assert entry.technologies == []
        assert entry.tags == []

    def test_missing_slug_rejected(self) -> None:
        with pytest.raises(ValidationError, match="slug"):
            ServiceEntry(id="svc-1", name="Service")  # type: ignore[call-arg]


class TestPortfolioData:
    def test_construction(self, basics_dict: dict[str, object]) -> None:
        basics = Basics.model_validate(basics_dict)
        portfolio = PortfolioData(
            basics=basics,
            work=[],
            education=[],
            skills=[],
            certificates=[],
            projects=[],
            volunteer=[],
            publications=[],
            languages=[],
            interests=None,
            services=[],
        )
        assert portfolio.basics.name == "Jane Doe"
        assert portfolio.work == []
        assert portfolio.interests is None

    def test_with_interests(self, basics_dict: dict[str, object]) -> None:
        basics = Basics.model_validate(basics_dict)
        interests = InterestData(
            hobbies=[Hobby(name="Hiking")],
            fun_facts=["Fun fact."],
        )
        portfolio = PortfolioData(
            basics=basics,
            work=[],
            education=[],
            skills=[],
            certificates=[],
            projects=[],
            volunteer=[],
            publications=[],
            languages=[],
            interests=interests,
            services=[],
        )
        assert portfolio.interests is not None
        assert portfolio.interests.hobbies[0].name == "Hiking"

    def test_frozen(self, basics_dict: dict[str, object]) -> None:
        basics = Basics.model_validate(basics_dict)
        portfolio = PortfolioData(
            basics=basics,
            work=[],
            education=[],
            skills=[],
            certificates=[],
            projects=[],
            volunteer=[],
            publications=[],
            languages=[],
            interests=None,
            services=[],
        )
        with pytest.raises(AttributeError):
            portfolio.basics = basics  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Structured output models
# ---------------------------------------------------------------------------


def _make_curation_dict(**overrides: Any) -> dict[str, Any]:
    """Build a minimal valid ResumeCuration dict with overrides."""
    base: dict[str, Any] = {
        "summary": "A seasoned engineer and founder of Perts Foundry LLC "
        "with 10 years of experience in cloud "
        "infrastructure and DevOps.",
        "suggested_label": "Senior Engineer",
        "company_slug": "test-co",
        "work_highlights": [
            {"work_id": "acme-senior-engineer", "highlight_ids": ["acme-deployed-k8s"]},
        ],
        "skills": [{"skill_id": "cloud-aws", "keywords": ["EKS"]}],
        "projects": ["infra-toolkit"],
    }
    base.update(overrides)
    return base


class TestWorkHighlightRanking:
    def test_valid(self) -> None:
        whr = WorkHighlightRanking(
            work_id="acme-senior-engineer",
            highlight_ids=["acme-deployed-k8s"],
        )
        assert whr.work_id == "acme-senior-engineer"
        assert whr.highlight_ids == ["acme-deployed-k8s"]

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WorkHighlightRanking.model_validate(
                {"work_id": "test", "highlight_ids": [], "extra": "forbidden"}
            )

    def test_frozen(self) -> None:
        whr = WorkHighlightRanking(work_id="test", highlight_ids=[])
        with pytest.raises(ValidationError):
            whr.work_id = "changed"


class TestSkillRanking:
    def test_valid_construction(self) -> None:
        sr = SkillRanking.model_validate(
            {"skill_id": "cloud-aws", "keywords": ["EKS", "S3"]}
        )
        assert sr.skill_id == "cloud-aws"
        assert sr.keywords == ["EKS", "S3"]

    def test_frozen(self) -> None:
        sr = SkillRanking.model_validate({"skill_id": "cloud-aws", "keywords": ["EKS"]})
        with pytest.raises(ValidationError):
            sr.skill_id = "changed"

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            SkillRanking.model_validate(
                {"skill_id": "cloud-aws", "keywords": ["EKS"], "extra": True}
            )

    def test_empty_keywords_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SkillRanking.model_validate({"skill_id": "cloud-aws", "keywords": []})


class TestResumeCuration:
    def test_valid_from_dict(self) -> None:
        curation = ResumeCuration.model_validate(
            _make_curation_dict(
                company_slug="acme-corp",
            )
        )
        assert curation.company_slug == "acme-corp"
        assert len(curation.work_highlights) == 1
        assert len(curation.skills) == 1
        assert curation.skills[0].skill_id == "cloud-aws"
        assert curation.skills[0].keywords == ["EKS"]
        assert curation.projects == ["infra-toolkit"]

    def test_extra_fields_rejected(self) -> None:
        d = _make_curation_dict(extra="forbidden")
        with pytest.raises(ValidationError):
            ResumeCuration.model_validate(d)

    def test_missing_summary_rejected(self) -> None:
        d = _make_curation_dict()
        del d["summary"]
        with pytest.raises(ValidationError, match="summary"):
            ResumeCuration.model_validate(d)

    def test_company_slug_invalid_pattern_rejected(self) -> None:
        with pytest.raises(ValidationError, match="company_slug"):
            ResumeCuration.model_validate(
                _make_curation_dict(company_slug="Invalid Company!")
            )

    def test_company_slug_max_length_64_accepted(self) -> None:
        slug = "a" * 64
        curation = ResumeCuration.model_validate(_make_curation_dict(company_slug=slug))
        assert curation.company_slug == slug

    def test_company_slug_over_64_chars_rejected(self) -> None:
        with pytest.raises(ValidationError, match="company_slug"):
            ResumeCuration.model_validate(_make_curation_dict(company_slug="a" * 65))

    def test_suggested_label_max_length_accepted(self) -> None:
        label = "A" * 60
        curation = ResumeCuration.model_validate(
            _make_curation_dict(suggested_label=label)
        )
        assert curation.suggested_label == label

    def test_suggested_label_over_max_length_rejected(self) -> None:
        with pytest.raises(ValidationError, match="suggested_label"):
            ResumeCuration.model_validate(_make_curation_dict(suggested_label="A" * 61))

    def test_suggested_label_empty_rejected(self) -> None:
        with pytest.raises(ValidationError, match="suggested_label"):
            ResumeCuration.model_validate(_make_curation_dict(suggested_label=""))

    def test_empty_skills_and_projects_valid(self) -> None:
        curation = ResumeCuration.model_validate(
            _make_curation_dict(skills=[], projects=[])
        )
        assert curation.skills == []
        assert curation.projects == []

    def test_empty_work_highlights_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ResumeCuration.model_validate(_make_curation_dict(work_highlights=[]))

    def test_field_order(self) -> None:
        assert tuple(ResumeCuration.model_fields) == (
            "summary",
            "suggested_label",
            "company_slug",
            "work_highlights",
            "skills",
            "projects",
            "work_highlight_weights",
            "work_highlight_weights_raw",
            "trim_priority",
        )

    def test_work_highlight_weights_default_to_empty(self) -> None:
        curation = ResumeCuration.model_validate(_make_curation_dict())
        assert curation.work_highlight_weights == {}

    def test_trim_priority_default_to_empty(self) -> None:
        curation = ResumeCuration.model_validate(_make_curation_dict())
        assert curation.trim_priority == []

    def test_work_highlight_weights_accept_valid_range(self) -> None:
        curation = ResumeCuration.model_validate(
            _make_curation_dict(work_highlight_weights={"acme-senior-engineer": 1.5})
        )
        assert curation.work_highlight_weights == {"acme-senior-engineer": 1.5}

    def test_work_highlight_weights_accept_lower_boundary(self) -> None:
        # The 0.5 boundary is inclusive per the validator's
        # ``0.5 <= weight <= 1.5`` check. Pinning it prevents an
        # accidental strict-comparison regression.
        curation = ResumeCuration.model_validate(
            _make_curation_dict(work_highlight_weights={"acme-senior-engineer": 0.5})
        )
        assert curation.work_highlight_weights == {"acme-senior-engineer": 0.5}

    def test_work_highlight_weights_accept_upper_boundary(self) -> None:
        # The 1.5 boundary is inclusive (matches per_entry_emit_cap
        # multiplier so weights at the ceiling stay effective).
        curation = ResumeCuration.model_validate(
            _make_curation_dict(work_highlight_weights={"acme-senior-engineer": 1.5})
        )
        assert curation.work_highlight_weights == {"acme-senior-engineer": 1.5}

    def test_work_highlight_weights_accept_unit_weight_no_op(self) -> None:
        # weight=1.0 is the no-op default — the most common production
        # value. Pinning ensures the validator never accidentally
        # treats 1.0 as a sentinel that should be stripped.
        curation = ResumeCuration.model_validate(
            _make_curation_dict(work_highlight_weights={"acme-senior-engineer": 1.0})
        )
        assert curation.work_highlight_weights == {"acme-senior-engineer": 1.0}

    def test_work_highlight_weights_clamp_above_max(self) -> None:
        """Out-of-range above MAX clamps to MAX; raw value preserved."""
        weights = {"acme-senior-engineer": 1.8}
        curation = ResumeCuration.model_validate(
            _make_curation_dict(work_highlight_weights=weights)
        )
        assert curation.work_highlight_weights == {"acme-senior-engineer": 1.5}
        assert curation.work_highlight_weights_raw == {"acme-senior-engineer": 1.8}

    def test_work_highlight_weights_clamp_below_min(self) -> None:
        """Out-of-range below MIN clamps to MIN; raw value preserved."""
        weights = {"acme-senior-engineer": 0.3}
        curation = ResumeCuration.model_validate(
            _make_curation_dict(work_highlight_weights=weights)
        )
        assert curation.work_highlight_weights == {"acme-senior-engineer": 0.5}
        assert curation.work_highlight_weights_raw == {"acme-senior-engineer": 0.3}

    def test_work_highlight_weights_clamp_far_over_max(self) -> None:
        """Far-over-range value still clamps to MAX, not partway."""
        weights = {"acme-senior-engineer": 2.5, "other-id": 5.0}
        curation = ResumeCuration.model_validate(
            _make_curation_dict(work_highlight_weights=weights)
        )
        assert curation.work_highlight_weights == {
            "acme-senior-engineer": 1.5,
            "other-id": 1.5,
        }
        assert curation.work_highlight_weights_raw == {
            "acme-senior-engineer": 2.5,
            "other-id": 5.0,
        }

    def test_work_highlight_weights_clamp_negative(self) -> None:
        """Negative weight clamps to MIN (not zero, not absolute)."""
        weights = {"acme-senior-engineer": -0.4}
        curation = ResumeCuration.model_validate(
            _make_curation_dict(work_highlight_weights=weights)
        )
        assert curation.work_highlight_weights == {"acme-senior-engineer": 0.5}
        assert curation.work_highlight_weights_raw == {"acme-senior-engineer": -0.4}

    def test_work_highlight_weights_just_above_max(self) -> None:
        """Boundary: a hair above MAX clamps to MAX."""
        weights = {"acme-senior-engineer": 1.500001}
        curation = ResumeCuration.model_validate(
            _make_curation_dict(work_highlight_weights=weights)
        )
        assert curation.work_highlight_weights == {"acme-senior-engineer": 1.5}

    def test_work_highlight_weights_just_below_max(self) -> None:
        """Boundary: a hair below MAX passes through unchanged."""
        weights = {"acme-senior-engineer": 1.499}
        curation = ResumeCuration.model_validate(
            _make_curation_dict(work_highlight_weights=weights)
        )
        assert curation.work_highlight_weights == {"acme-senior-engineer": 1.499}
        # In-range emissions still populate raw (validator runs
        # uniformly; raw == clamped is the happy path).
        assert curation.work_highlight_weights_raw == {"acme-senior-engineer": 1.499}

    def test_work_highlight_weights_raw_empty_when_no_weights(self) -> None:
        """No AI emission -> both fields empty."""
        curation = ResumeCuration.model_validate(_make_curation_dict())
        assert curation.work_highlight_weights == {}
        assert curation.work_highlight_weights_raw == {}

    def test_work_highlight_weights_raw_respects_existing_value(self) -> None:
        """Reloading a curation_log.json that already carries _raw must
        not overwrite it with the (already clamped) primary field. The
        pre-validator skips capture when _raw is already populated."""
        data = _make_curation_dict(
            work_highlight_weights={"acme-senior-engineer": 1.5},
        )
        data["work_highlight_weights_raw"] = {"acme-senior-engineer": 1.8}
        curation = ResumeCuration.model_validate(data)
        assert curation.work_highlight_weights == {"acme-senior-engineer": 1.5}
        # _raw preserved verbatim from the persisted log, NOT mirrored
        # from work_highlight_weights.
        assert curation.work_highlight_weights_raw == {"acme-senior-engineer": 1.8}

    def test_work_highlight_weights_validate_does_not_mutate_input(self) -> None:
        """The pre-validator must not leak the ``_raw`` mirror back
        onto the caller's dict reference."""
        data = _make_curation_dict(work_highlight_weights={"acme-senior-engineer": 1.8})
        before_keys = set(data.keys())
        ResumeCuration.model_validate(data)
        assert set(data.keys()) == before_keys
        assert "work_highlight_weights_raw" not in data

    def test_work_highlight_weights_non_dict_yields_typed_error(self) -> None:
        """Non-dict input must surface Pydantic's type error, not the
        opaque ``TypeError`` from ``dict(...)`` inside the pre-validator."""
        data = _make_curation_dict()
        data["work_highlight_weights"] = "not-a-dict"
        with pytest.raises(ValidationError, match=r"dictionary|valid dict"):
            ResumeCuration.model_validate(data)

    def test_trim_priority_accepts_valid_items(self) -> None:
        curation = ResumeCuration.model_validate(
            _make_curation_dict(trim_priority=["certificates", "projects"])
        )
        assert curation.trim_priority == ["certificates", "projects"]

    def test_trim_priority_rejects_unknown_item(self) -> None:
        with pytest.raises(ValidationError, match="not in allowed set"):
            ResumeCuration.model_validate(
                _make_curation_dict(trim_priority=["not-a-section"])
            )

    def test_trim_priority_rejects_pinned_items(self) -> None:
        # ``interests`` and ``highlight`` are pinned by the renderer
        # and not exposed to the AI; validator rejects them.
        with pytest.raises(ValidationError, match="not in allowed set"):
            ResumeCuration.model_validate(
                _make_curation_dict(trim_priority=["interests"])
            )
        with pytest.raises(ValidationError, match="not in allowed set"):
            ResumeCuration.model_validate(
                _make_curation_dict(trim_priority=["highlight"])
            )

    def test_trim_priority_rejects_duplicates(self) -> None:
        with pytest.raises(ValidationError, match="duplicate"):
            ResumeCuration.model_validate(
                _make_curation_dict(trim_priority=["projects", "projects"])
            )

    def test_control_chars_in_summary_rejected(self) -> None:
        with pytest.raises(ValidationError, match="control characters"):
            ResumeCuration.model_validate(
                _make_curation_dict(summary="Has a null\x00byte")
            )

    def test_control_chars_in_label_rejected(self) -> None:
        with pytest.raises(ValidationError, match="control characters"):
            ResumeCuration.model_validate(
                _make_curation_dict(suggested_label="Bad\x1flabel")
            )

    @pytest.mark.parametrize(
        ("char", "name"),
        [
            ("­", "SOFT HYPHEN"),
            ("​", "ZERO WIDTH SPACE"),
            ("‌", "ZWNJ"),
            ("‍", "ZWJ"),
            ("‎", "LRM"),
            ("‏", "RLM"),
            ("﻿", "BOM / ZWNBSP"),
        ],
    )
    def test_invisible_chars_in_summary_rejected(self, char: str, name: str) -> None:
        # Defense in depth for the soft-hyphen fix: Typst auto-hyphenation is
        # disabled, but if a contributor pastes a SHY (or any zero-width
        # formatting char) into the LLM output or a portfolio YAML field,
        # the validator must catch it before it reaches the rendered PDF.
        with pytest.raises(ValidationError, match="control characters"):
            ResumeCuration.model_validate(
                _make_curation_dict(summary=f"Has a {name}{char}character")
            )

    def test_priority_field_on_education(self) -> None:
        edu = EducationEntry.model_validate(
            {"id": "umw-bs-cs", "institution": "UMW", "priority": 1}
        )
        assert edu.priority == 1

    def test_priority_field_default_none(self) -> None:
        edu = EducationEntry.model_validate({"id": "umw-bs-cs", "institution": "UMW"})
        assert edu.priority is None

    def test_priority_field_on_certificate(self) -> None:
        cert = CertificateEntry.model_validate(
            {"id": "cka", "name": "CKA", "date": "2024-01", "priority": 2}
        )
        assert cert.priority == 2


# ---------------------------------------------------------------------------
# Parametrized common behavior tests
# ---------------------------------------------------------------------------

_MODELS_WITH_ID: list[tuple[type[BaseModel], dict[str, Any]]] = [
    (EducationEntry, {"id": "umw-bs-cs", "institution": "UMW"}),
    (SkillEntry, {"id": "cloud-aws", "name": "AWS"}),
    (CertificateEntry, {"id": "cka", "name": "CKA", "date": "2023"}),
    (ProjectEntry, {"id": "my-proj", "name": "My Project"}),
    (VolunteerEntry, {"id": "spca", "organization": "SPCA"}),
    (PublicationEntry, {"id": "tech-talk", "name": "Talk"}),
    (LanguageEntry, {"id": "english", "language": "English"}),
    (ServiceEntry, {"id": "svc-1", "name": "Service", "slug": "service"}),
]


class TestCommonModelBehaviors:
    """Shared behavior tests across all entry models with an id field."""

    @pytest.mark.parametrize(
        ("model_cls", "valid_kwargs"),
        _MODELS_WITH_ID,
        ids=lambda v: v.__name__ if isinstance(v, type) else "",
    )
    def test_invalid_id_rejected(
        self, model_cls: type[BaseModel], valid_kwargs: dict[str, Any]
    ) -> None:
        for bad_id in ["Invalid", "has space", "-leading"]:
            with pytest.raises(ValidationError, match="id"):
                model_cls(**{**valid_kwargs, "id": bad_id})

    @pytest.mark.parametrize(
        ("model_cls", "valid_kwargs"),
        _MODELS_WITH_ID,
        ids=lambda v: v.__name__ if isinstance(v, type) else "",
    )
    def test_frozen(
        self, model_cls: type[BaseModel], valid_kwargs: dict[str, Any]
    ) -> None:
        instance = model_cls(**valid_kwargs)
        with pytest.raises(ValidationError):
            instance.id = "changed"  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        ("model_cls", "valid_kwargs"),
        _MODELS_WITH_ID,
        ids=lambda v: v.__name__ if isinstance(v, type) else "",
    )
    def test_missing_required_fields(
        self, model_cls: type[BaseModel], valid_kwargs: dict[str, Any]
    ) -> None:
        with pytest.raises(ValidationError):
            model_cls(id="valid-id")

    @pytest.mark.parametrize(
        ("model_cls", "valid_kwargs"),
        _MODELS_WITH_ID,
        ids=lambda v: v.__name__ if isinstance(v, type) else "",
    )
    def test_extra_fields_ignored(
        self, model_cls: type[BaseModel], valid_kwargs: dict[str, Any]
    ) -> None:
        instance = model_cls(**{**valid_kwargs, "unknown_extra": "dropped"})
        assert not hasattr(instance, "unknown_extra")


# ---------------------------------------------------------------------------
# Cover letter schemas and policy validator
# ---------------------------------------------------------------------------


from curator.exceptions import CurationValidationError  # noqa: E402
from curator.models import (  # noqa: E402
    CoverLetterCuration,
    ResumeCurationWithCoverLetter,
    validate_cover_letter,
)


def _minimal_portfolio() -> PortfolioData:
    return PortfolioData(
        basics=Basics(name="Seth"),
        work=[],
        education=[],
        skills=[],
        certificates=[],
        projects=[],
        volunteer=[],
        publications=[],
        languages=[],
        interests=None,
        services=[],
    )


from tests.helpers import (  # noqa: E402
    body_paragraph_embedding,
    valid_cover_letter_kwargs,
)


def _valid_letter_kwargs() -> dict[str, Any]:
    """Local alias for ``tests.helpers.valid_cover_letter_kwargs``.

    Kept so tests in this file don't need to rename; new tests should
    import ``valid_cover_letter_kwargs`` directly from ``tests.helpers``.
    """
    return valid_cover_letter_kwargs()


class TestCoverLetterCurationStructure:
    def test_valid_letter_constructs(self) -> None:
        letter = CoverLetterCuration(**_valid_letter_kwargs())
        assert letter.sign_off == "Sincerely"

    def test_is_template_field_removed(self) -> None:
        """The is_template field was deleted when the TEMPLATE banner was
        retired. Any stray is_template input should be rejected under
        extra='forbid'."""
        assert "is_template" not in CoverLetterCuration.model_fields
        kwargs = _valid_letter_kwargs()
        kwargs["is_template"] = True
        with pytest.raises(ValidationError):
            CoverLetterCuration(**kwargs)

    def test_frozen(self) -> None:
        letter = CoverLetterCuration(**_valid_letter_kwargs())
        with pytest.raises(ValidationError):
            letter.sign_off = "Cheers"

    @pytest.mark.parametrize(
        ("field", "bad_value", "match"),
        [
            ("salutation", "Dear Hiring Manager", "comma"),
            ("sign_off", "Sincerely,", "trailing comma"),
            ("sign_off", "Cheers", "must be one of"),
            ("sign_off", "Warmly", "must be one of"),
        ],
    )
    def test_invalid_surface_structure_rejected(
        self, field: str, bad_value: str, match: str
    ) -> None:
        kwargs = _valid_letter_kwargs()
        kwargs[field] = bad_value
        with pytest.raises(ValidationError, match=match):
            CoverLetterCuration(**kwargs)

    @pytest.mark.parametrize(
        ("field", "injected"),
        [("opening", "em\u2014dash"), ("closing", "en\u2013dash")],
    )
    def test_em_dash_in_prose_rejected(self, field: str, injected: str) -> None:
        kwargs = _valid_letter_kwargs()
        kwargs[field] = kwargs[field] + " " + injected + " final words. " * 6
        with pytest.raises(ValidationError, match="em dash"):
            CoverLetterCuration(**kwargs)

    def test_em_dash_in_body_paragraph_rejected(self) -> None:
        kwargs = _valid_letter_kwargs()
        kwargs["body_paragraphs"] = [
            kwargs["body_paragraphs"][0] + " em\u2014bad text " * 4,
            kwargs["body_paragraphs"][1],
        ]
        with pytest.raises(ValidationError, match="em dash"):
            CoverLetterCuration(**kwargs)

    @pytest.mark.parametrize(
        "char",
        [
            "\u00ad",  # SOFT HYPHEN
            "\u200b",  # ZERO WIDTH SPACE
            "\u200c",  # ZWNJ
            "\u200d",  # ZWJ
            "\u200e",  # LRM
            "\u200f",  # RLM
            "\ufeff",  # BOM / ZWNBSP
            "\x00",  # NUL (C0 baseline)
            "\x1f",  # US (C0 baseline)
        ],
    )
    @pytest.mark.parametrize("field", ["salutation", "opening", "closing", "sign_off"])
    def test_invisible_chars_in_scalar_field_rejected(
        self, field: str, char: str
    ) -> None:
        kwargs = _valid_letter_kwargs()
        # Salutation must end in comma; sign_off enum-restricted; both must
        # remain structurally valid so the control-char validator is the
        # one that fires (not a sibling structural rule).
        kwargs[field] = kwargs[field][:-1] + char + kwargs[field][-1:]
        with pytest.raises(ValidationError, match="control characters"):
            CoverLetterCuration(**kwargs)

    @pytest.mark.parametrize(
        "char",
        [
            "\u00ad",
            "\u200b",
            "\u200c",
            "\u200d",
            "\u200e",
            "\u200f",
            "\ufeff",
            "\x00",
            "\x1f",
        ],
    )
    def test_invisible_chars_in_body_paragraph_rejected(self, char: str) -> None:
        kwargs = _valid_letter_kwargs()
        # Inject into the middle of body[0] so paragraph-band length and
        # surface validators stay satisfied; only the control-char check
        # should fire.
        body0 = kwargs["body_paragraphs"][0]
        kwargs["body_paragraphs"] = [
            body0[: len(body0) // 2] + char + body0[len(body0) // 2 :],
            kwargs["body_paragraphs"][1],
        ]
        with pytest.raises(ValidationError, match="control characters"):
            CoverLetterCuration(**kwargs)

    def test_body_paragraph_count_too_few_rejected(self) -> None:
        kwargs = _valid_letter_kwargs()
        kwargs["body_paragraphs"] = kwargs["body_paragraphs"][:1]
        with pytest.raises(ValidationError, match="body_paragraphs must have"):
            CoverLetterCuration(**kwargs)

    def test_body_paragraph_count_too_many_rejected(self) -> None:
        kwargs = _valid_letter_kwargs()
        kwargs["body_paragraphs"] = [*kwargs["body_paragraphs"], "extra para"] * 2
        with pytest.raises(ValidationError):
            CoverLetterCuration(**kwargs)


class TestResumeCurationWithCoverLetter:
    def test_round_trip(self) -> None:
        resume = ResumeCuration(
            summary=(
                "Platform engineer and founder of Perts Foundry LLC "
                "with eight years leading "
                "infrastructure rollouts."
            ),
            suggested_label="Staff DevOps Engineer",
            company_slug="beta-corp",
            work_highlights=[
                WorkHighlightRanking(work_id="acme-eng", highlight_ids=["h1"])
            ],
            skills=[],
            projects=[],
        )
        letter = CoverLetterCuration(**_valid_letter_kwargs())
        wrapper = ResumeCurationWithCoverLetter(resume=resume, cover_letter=letter)
        reloaded = ResumeCurationWithCoverLetter.model_validate(wrapper.model_dump())
        assert reloaded.resume.company_slug == "beta-corp"
        assert reloaded.cover_letter.sign_off == "Sincerely"


class TestValidateCoverLetter:
    def test_valid_letter_passes(self) -> None:
        letter = CoverLetterCuration(**_valid_letter_kwargs())
        validate_cover_letter(letter, _minimal_portfolio())

    def test_forbidden_phrase_rejected(self) -> None:
        kwargs = _valid_letter_kwargs()
        kwargs["body_paragraphs"][0] = body_paragraph_embedding(
            "I also bring a proven track record of shipping production systems."
        )
        letter = CoverLetterCuration(**kwargs)
        with pytest.raises(CurationValidationError, match="forbidden phrases"):
            validate_cover_letter(letter, _minimal_portfolio())

    def test_forbidden_word_whole_word_only(self) -> None:
        # "realm" matches; "realmwide" does not (whole-word boundary).
        kwargs = _valid_letter_kwargs()
        kwargs["body_paragraphs"][0] = body_paragraph_embedding(
            "I have deep expertise in this realm of distributed computing."
        )
        letter = CoverLetterCuration(**kwargs)
        with pytest.raises(CurationValidationError, match="forbidden words"):
            validate_cover_letter(letter, _minimal_portfolio())

    def test_forbidden_word_does_not_match_inside_larger_word(self) -> None:
        # "realm" is forbidden as a whole word; an invented compound that
        # contains it as a substring must NOT trip the matcher.
        kwargs = _valid_letter_kwargs()
        kwargs["body_paragraphs"][0] = body_paragraph_embedding(
            "I worked on realmwide tooling that supported the program."
        )
        letter = CoverLetterCuration(**kwargs)
        validate_cover_letter(letter, _minimal_portfolio())

    def test_forbidden_word_lowercase_metaphor_rejected(self) -> None:
        # SA-2 / [TEST-4] (2026-04-26): forbidden-word matching is now
        # case-sensitive against lowercase patterns, so capitalized
        # proper-noun occurrences (target company names that happen to
        # collide with a forbidden metaphor word, e.g. "Beacon, Inc.")
        # are exempted while lowercase metaphor uses still trip.
        kwargs = _valid_letter_kwargs()
        kwargs["body_paragraphs"][0] = body_paragraph_embedding(
            "I delve deeper into operational questions than most."
        )
        letter = CoverLetterCuration(**kwargs)
        with pytest.raises(CurationValidationError, match="forbidden words"):
            validate_cover_letter(letter, _minimal_portfolio())

    def test_forbidden_word_capitalized_proper_noun_exempted(self) -> None:
        # [TEST-4] regression: a target company whose name happens to
        # collide with a forbidden metaphor word ("Beacon, Inc." in this
        # test) should NOT trip the forbidden-word ban on the lowercase
        # metaphor "beacon".
        kwargs = _valid_letter_kwargs()
        kwargs["body_paragraphs"][0] = body_paragraph_embedding(
            "Beacon's commitment to operational excellence aligns "
            "with my multi-cloud infrastructure work."
        )
        letter = CoverLetterCuration(**kwargs)
        # Should NOT raise — capitalized proper noun is exempt.
        validate_cover_letter(letter, _minimal_portfolio())

    def test_placeholder_tokens_always_rejected(self) -> None:
        # Unfilled [COMPANY] / [TAILOR: ...] tokens must NEVER appear in the
        # final letter. Both static-mode and API-mode letters must be fully
        # filled in; the validator rejects any leftover placeholder.
        kwargs = _valid_letter_kwargs()
        kwargs["body_paragraphs"][0] = body_paragraph_embedding(
            "I would welcome the chance to apply these patterns at [COMPANY]."
        )
        letter = CoverLetterCuration(**kwargs)
        with pytest.raises(
            CurationValidationError, match="unfilled placeholder tokens"
        ):
            validate_cover_letter(letter, _minimal_portfolio())

    def test_placeholder_with_tailor_marker_rejected(self) -> None:
        kwargs = _valid_letter_kwargs()
        kwargs["closing"] = (
            "I would welcome a conversation. [TAILOR: replace this sentence "
            "with one company-specific reference before sending.] Thank you."
        )
        letter = CoverLetterCuration(**kwargs)
        with pytest.raises(
            CurationValidationError, match="unfilled placeholder tokens"
        ):
            validate_cover_letter(letter, _minimal_portfolio())

    def test_placeholder_in_salutation_rejected(self) -> None:
        """The default mistake `Dear [HIRING_MANAGER_NAME],` must fail.

        Placeholder scanning covers salutation + body scope; the
        salutation-only forbidden-phrase channel remains distinct.
        """
        kwargs = _valid_letter_kwargs()
        kwargs["salutation"] = "Dear [HIRING_MANAGER_NAME],"
        letter = CoverLetterCuration(**kwargs)
        with pytest.raises(
            CurationValidationError, match="unfilled placeholder tokens"
        ):
            validate_cover_letter(letter, _minimal_portfolio())

    def test_total_word_count_too_low_rejected(self) -> None:
        kwargs = _valid_letter_kwargs()
        kwargs["body_paragraphs"] = [
            "A short but valid body paragraph with enough words to pass "
            "the per-paragraph minimum but keep the total below the band "
            "and so trigger total-count failure for this test case here."
            " Extra padding words to stay in paragraph band.",
            "Another short paragraph also under cap.  " * 4,
        ]
        kwargs["opening"] = "Short opening sentence one. Short opening sentence two."
        kwargs["closing"] = "Short closing."
        letter = CoverLetterCuration(**kwargs)
        with pytest.raises(CurationValidationError, match="total word count"):
            validate_cover_letter(letter, _minimal_portfolio())

    def test_per_paragraph_word_count_too_high_warns_not_rejects(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Per-paragraph over-max is a soft warning (parallel to total
        # over-max). Letter still validates; warning logged.
        kwargs = _valid_letter_kwargs()
        huge = "word " * 135
        kwargs["body_paragraphs"][0] = huge
        # Shrink paragraph 2 so total stays under the soft-warn ceiling.
        kwargs["body_paragraphs"][1] = "word " * 45
        kwargs["opening"] = "word " * 50
        kwargs["closing"] = "word " * 50
        letter = CoverLetterCuration(**kwargs)
        # Does NOT raise (soft warn).
        validate_cover_letter(letter, _minimal_portfolio())

    def test_total_word_count_too_high_warns_not_rejects(self) -> None:
        # AR-7 / SA-6 (2026-04-26): the headline soft-warn behavior.
        # Total > COVER_LETTER_WORD_MAX must NOT raise on the API path
        # (default strict=False); it ships with a logger.warning. This
        # test pins the contract that paid API calls aren't reject-and-
        # discarded for a 5-10% overshoot.
        #
        # 2026-05-17: cap moved from 300 to 360; body paragraphs are
        # bounded at 90 hard, so the overshoot has to come from
        # opening/closing where per-section bands are not enforced.
        # Total here: 105 + 90 + 90 + 90 = 375 (15 over the new 360 cap).
        kwargs = _valid_letter_kwargs()
        kwargs["opening"] = "word " * 105
        kwargs["body_paragraphs"][0] = "word " * 90
        kwargs["body_paragraphs"][1] = "word " * 90
        kwargs["closing"] = "word " * 90
        letter = CoverLetterCuration(**kwargs)
        # Does NOT raise (soft warn on API path).
        validate_cover_letter(letter, _minimal_portfolio())

    def test_total_word_count_too_high_strict_rejects(self) -> None:
        # AR-6 (2026-04-26): static path passes strict=True. Total
        # over-max becomes a hard reject so hand-authored YAMLs fail
        # loudly instead of silently shipping.
        kwargs = _valid_letter_kwargs()
        kwargs["opening"] = "word " * 105
        kwargs["body_paragraphs"][0] = "word " * 90
        kwargs["body_paragraphs"][1] = "word " * 90
        kwargs["closing"] = "word " * 90
        letter = CoverLetterCuration(**kwargs)
        with pytest.raises(CurationValidationError, match="exceeds maximum"):
            validate_cover_letter(letter, _minimal_portfolio(), strict=True)

    def test_per_paragraph_word_count_too_low_rejected(self) -> None:
        # Under-min stays a hard reject: stunted body paragraphs fail
        # STAR structure and aren't recoverable.
        kwargs = _valid_letter_kwargs()
        kwargs["body_paragraphs"][0] = "word " * 10  # 10 < 40 min
        letter = CoverLetterCuration(**kwargs)
        with pytest.raises(
            CurationValidationError, match="below per-paragraph minimum"
        ):
            validate_cover_letter(letter, _minimal_portfolio())

    def test_salutation_to_whom_it_may_concern_rejected(self) -> None:
        kwargs = _valid_letter_kwargs()
        kwargs["salutation"] = "To Whom It May Concern,"
        letter = CoverLetterCuration(**kwargs)
        with pytest.raises(
            CurationValidationError, match="salutation contains forbidden phrase"
        ):
            validate_cover_letter(letter, _minimal_portfolio())

    def test_salutation_specific_phrase_not_matched_in_body(self) -> None:
        # "to whom it may concern" is salutation-scoped only; if it appears
        # in body prose (an unusual but possible quotation), the validator
        # must not raise on a salutation-scope failure.
        kwargs = _valid_letter_kwargs()
        kwargs["body_paragraphs"][0] = body_paragraph_embedding(
            "I once received a letter addressed simply to whom it may "
            "concern, which felt impersonal."
        )
        letter = CoverLetterCuration(**kwargs)
        # Validator should NOT raise: the phrase is body-allowed (not in
        # COVER_LETTER_FORBIDDEN_PHRASES) and salutation-only forbidden.
        validate_cover_letter(letter, _minimal_portfolio())

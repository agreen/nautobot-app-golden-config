"""Unit tests for tag support on nautobot_golden_config models."""

from unittest import mock

from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from nautobot.apps.testing import TestCase
from nautobot.core.jobs import BulkEditObjects  # core-import-update
from nautobot.dcim.models import Device
from nautobot.extras.models import JobResult, Tag
from nautobot.extras.utils import TaggableClassesQuery  # core-import-update

from nautobot_golden_config import forms, models, tables

from .conftest import create_device_data

# Model, bulk edit form, filter form and table for each taggable Golden Config model.
TAGGABLE_MODELS = (
    (
        models.ComplianceFeature,
        forms.ComplianceFeatureBulkEditForm,
        forms.ComplianceFeatureFilterForm,
        tables.ComplianceFeatureTable,
    ),
    (
        models.ComplianceRule,
        forms.ComplianceRuleBulkEditForm,
        forms.ComplianceRuleFilterForm,
        tables.ComplianceRuleTable,
    ),
    (
        models.ConfigPlan,
        forms.ConfigPlanBulkEditForm,
        forms.ConfigPlanFilterForm,
        tables.ConfigPlanTable,
    ),
    (
        models.ConfigRemove,
        forms.ConfigRemoveBulkEditForm,
        forms.ConfigRemoveFilterForm,
        tables.ConfigRemoveTable,
    ),
    (
        models.ConfigReplace,
        forms.ConfigReplaceBulkEditForm,
        forms.ConfigReplaceFilterForm,
        tables.ConfigReplaceTable,
    ),
    (
        models.GoldenConfig,
        forms.GoldenConfigBulkEditForm,
        forms.GoldenConfigFilterForm,
        tables.GoldenConfigTable,
    ),
    (
        models.GoldenConfigSetting,
        forms.GoldenConfigSettingBulkEditForm,
        forms.GoldenConfigSettingFilterForm,
        tables.GoldenConfigSettingTable,
    ),
    (
        models.RemediationSetting,
        forms.RemediationSettingBulkEditForm,
        forms.RemediationSettingFilterForm,
        tables.RemediationSettingTable,
    ),
)


class TaggableModelUITestCase(TestCase):
    """Test that every taggable Golden Config model exposes tags throughout the UI."""

    def test_models_are_taggable(self):
        """All Golden Config models, including the device pivoted ConfigCompliance, accept tags."""
        taggable_content_types = TaggableClassesQuery().as_queryset()
        for model in [model for model, *_ in TAGGABLE_MODELS] + [models.ConfigCompliance]:
            with self.subTest(model=model.__name__):
                self.assertIn(ContentType.objects.get_for_model(model), taggable_content_types)

    def test_bulk_edit_forms_support_tags(self):
        """Tags can be added and removed from the bulk edit form of every taggable model."""
        for model, bulk_edit_form_class, _, _ in TAGGABLE_MODELS:
            with self.subTest(model=model.__name__):
                form = bulk_edit_form_class(model)
                self.assertIn("add_tags", form.fields)
                self.assertIn("remove_tags", form.fields)

    def test_filter_forms_support_tags(self):
        """Every list view, ConfigCompliance included, can be filtered by tag."""
        filter_form_classes = [filter_form_class for _, _, filter_form_class, _ in TAGGABLE_MODELS]
        filter_form_classes.append(forms.ConfigComplianceFilterForm)
        for filter_form_class in filter_form_classes:
            with self.subTest(form=filter_form_class.__name__):
                self.assertIn("tags", filter_form_class().fields)

    def test_tables_have_non_default_tags_column(self):
        """Tags are a configurable table column but are not displayed by default."""
        for model, _, _, table_class in TAGGABLE_MODELS:
            with self.subTest(model=model.__name__):
                table = table_class(model.objects.none())
                self.assertIn("tags", [column_name for column_name, _ in table.configurable_columns])
                self.assertNotIn("tags", table.visible_columns)


class BulkEditTagsTestCase(TestCase):
    """Test bulk editing tags through the UI."""

    # View permissions are required as well because bulk edit form fields are restricted to viewable objects.
    user_permissions = [
        "extras.view_tag",
        "nautobot_golden_config.change_compliancerule",
        "nautobot_golden_config.view_compliancerule",
    ]

    @classmethod
    def setUpTestData(cls):
        """Set up base objects."""
        create_device_data()
        platform = Device.objects.first().platform
        cls.rules = [
            models.ComplianceRule.objects.create(
                feature=models.ComplianceFeature.objects.create(name=f"Feature {num}", slug=f"feature-{num}"),
                platform=platform,
                config_ordered=True,
                match_config=f"match config {num}",
            )
            for num in range(2)
        ]
        cls.keep_tag = Tag.objects.create(name="Keep Tag")
        cls.keep_tag.content_types.add(ContentType.objects.get_for_model(models.ComplianceRule))
        cls.remove_tag = Tag.objects.create(name="Remove Tag")
        cls.remove_tag.content_types.add(ContentType.objects.get_for_model(models.ComplianceRule))
        for rule in cls.rules:
            rule.tags.add(cls.remove_tag)

    def test_bulk_edit_passes_tags_to_job(self):
        """Tags selected in the bulk edit form reach the "Bulk Edit Objects" system job."""
        with mock.patch.object(JobResult, "enqueue_job", wraps=JobResult.enqueue_job) as mock_enqueue_job:
            response = self.client.post(
                reverse("plugins:nautobot_golden_config:compliancerule_bulk_edit"),
                data={
                    "pk": [rule.pk for rule in self.rules],
                    "_apply": True,
                    "add_tags": [self.keep_tag.pk],
                    "remove_tags": [self.remove_tag.pk],
                },
            )
        # A valid bulk edit hands off to the system job and redirects to its result.
        self.assertHttpStatus(response, 302)
        mock_enqueue_job.assert_called_once()

        job_form = BulkEditObjects.as_form(BulkEditObjects.deserialize_data(mock_enqueue_job.call_args.kwargs))
        self.assertTrue(job_form.is_valid())
        bulk_edit_form = forms.ComplianceRuleBulkEditForm(models.ComplianceRule, job_form.cleaned_data["form_data"])
        self.assertTrue(bulk_edit_form.is_valid())
        self.assertEqual(list(bulk_edit_form.cleaned_data["add_tags"]), [self.keep_tag])
        self.assertEqual(list(bulk_edit_form.cleaned_data["remove_tags"]), [self.remove_tag])

import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone

from portal.constants import DECLARATION, DECLARATION_VERSION


def draft_expiry():
    return timezone.now() + timedelta(hours=24)


def default_currencies():
    return ["EUR"]


class Event(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    cost_center = models.CharField(max_length=80)
    cost_object = models.CharField(max_length=80, blank=True)
    finance_note = models.CharField(max_length=300, blank=True)
    recipient = models.EmailField()
    support_email = models.EmailField()
    instructions = models.TextField(blank=True)
    privacy_notice = models.TextField()
    privacy_version = models.CharField(max_length=80)
    declaration = models.TextField(default=DECLARATION)
    declaration_version = models.CharField(max_length=80, default=DECLARATION_VERSION)
    allowed_currencies = models.JSONField(default=default_currencies)
    travel_required = models.BooleanField(default=True)
    trip_start = models.DateField(null=True, blank=True)
    trip_end = models.DateField(null=True, blank=True)
    opens_at = models.DateTimeField(null=True, blank=True)
    closes_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    config_version = models.PositiveIntegerField(default=1)

    def is_open(self):
        now = timezone.now()
        return (
            self.active
            and (not self.opens_at or now >= self.opens_at)
            and (not self.closes_at or now <= self.closes_at)
        )

    def snapshot(self):
        fields = [
            "name",
            "slug",
            "cost_center",
            "cost_object",
            "finance_note",
            "recipient",
            "support_email",
            "declaration",
            "declaration_version",
            "privacy_notice",
            "privacy_version",
            "allowed_currencies",
            "travel_required",
            "config_version",
        ]
        return {field: getattr(self, field) for field in fields}


class DraftAdmission(models.Model):
    """A shared creation budget, keyed by a secret digest of the client network."""

    key = models.CharField(max_length=64, primary_key=True)
    window_start = models.DateTimeField(default=timezone.now, db_index=True)
    count = models.PositiveIntegerField(default=0)
    upload_count = models.PositiveIntegerField(default=0)
    upload_bytes = models.PositiveBigIntegerField(default=0)
    upload_window_start = models.DateTimeField(default=timezone.now)
    upload_window_count = models.PositiveIntegerField(default=0)


class Draft(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT)
    session_hash = models.CharField(max_length=64)
    token_hash = models.CharField(max_length=64)
    config_version = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(default=draft_expiry, db_index=True)
    claim = models.OneToOneField(
        "Claim", on_delete=models.SET_NULL, null=True, blank=True, related_name="accepted_draft"
    )
    payload_hash = models.CharField(max_length=64, blank=True)
    upload_count = models.PositiveIntegerField(default=0)
    upload_bytes = models.PositiveBigIntegerField(default=0)


class Claim(models.Model):
    class State(models.TextChoices):
        RECEIVED = "received"
        RENDERING = "rendering"
        READY = "ready"
        FAILED = "processing_failed"
        PURGING = "purging"
        PURGED = "purged"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT)
    full_name = models.CharField(max_length=200)
    postal_address = models.TextField()
    email = models.EmailField()
    iban = models.CharField(max_length=34)
    purpose = models.TextField()
    trip_location = models.CharField(max_length=200, blank=True)
    trip_start = models.DateField(null=True, blank=True)
    trip_end = models.DateField(null=True, blank=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    snapshot = models.JSONField()
    totals = models.JSONField(default=dict)
    state = models.CharField(
        max_length=24, choices=State.choices, default=State.RECEIVED, db_index=True
    )
    pdf_key = models.CharField(max_length=200, blank=True)
    pdf_hash = models.CharField(max_length=64, blank=True)
    pdf_size = models.PositiveIntegerField(default=0)
    pdf_page_count = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=80, blank=True)
    retention_due = models.DateTimeField(null=True, blank=True, db_index=True)
    resolution_due = models.DateTimeField(default=timezone.now)
    purged_at = models.DateTimeField(null=True, blank=True)
    supersedes = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replacements"
    )

    @property
    def reference(self):
        return str(self.id)


class Expense(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="expenses")
    position = models.PositiveSmallIntegerField()
    merchant = models.CharField(max_length=200)
    purpose = models.CharField(max_length=500)
    receipt_reference = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["claim", "position"], name="expense_position_unique"),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="expense_amount_positive"
            ),
        ]


class Upload(models.Model):
    class State(models.TextChoices):
        PENDING = "pending"
        READY = "ready"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    draft = models.ForeignKey(Draft, on_delete=models.CASCADE, related_name="uploads")
    claim = models.ForeignKey(
        Claim, null=True, blank=True, on_delete=models.CASCADE, related_name="uploads"
    )
    expense_id = models.UUIDField(null=True, blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    kind = models.CharField(max_length=12, default="receipt")
    original_name = models.CharField(max_length=200)
    source_key = models.CharField(max_length=200)
    normalized_key = models.CharField(max_length=200, blank=True)
    normalized_size = models.PositiveIntegerField(default=0)
    reserved_bytes = models.PositiveIntegerField(default=0)
    size = models.PositiveIntegerField()
    checksum = models.CharField(max_length=64)
    media_type = models.CharField(max_length=40, blank=True)
    pages = models.PositiveIntegerField(default=0)
    state = models.CharField(max_length=12, choices=State.choices, default=State.PENDING)
    error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(default=timezone.now)


class Delivery(models.Model):
    claim = models.OneToOneField(Claim, on_delete=models.CASCADE, related_name="delivery")
    recipient = models.EmailField()
    state = models.CharField(max_length=24, default="pending", db_index=True)
    message_id = models.CharField(max_length=100, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class DeliveryAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="attempts")
    state = models.CharField(max_length=24, default="sending")
    message_id = models.CharField(max_length=100, null=True, blank=True, unique=True)
    error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    last_checked_at = models.DateTimeField(null=True, blank=True, db_index=True)


class DeliveryEvent(models.Model):
    provider_id = models.CharField(max_length=100, unique=True)
    message_id = models.CharField(max_length=100, blank=True, db_index=True)
    attempt = models.ForeignKey(DeliveryAttempt, null=True, blank=True, on_delete=models.SET_NULL)
    kind = models.CharField(max_length=80)
    recipient_hash = models.CharField(max_length=64)
    attempt_tag = models.CharField(max_length=80, blank=True)
    provider_timestamp = models.DateTimeField()
    received_at = models.DateTimeField(default=timezone.now)
    applied = models.BooleanField(default=False)
    last_checked_at = models.DateTimeField(null=True, blank=True, db_index=True)


class Job(models.Model):
    key = models.CharField(max_length=160, unique=True)
    kind = models.CharField(max_length=20)
    upload = models.ForeignKey(Upload, null=True, blank=True, on_delete=models.CASCADE)
    claim = models.ForeignKey(Claim, null=True, blank=True, on_delete=models.CASCADE)
    delivery = models.ForeignKey(Delivery, null=True, blank=True, on_delete=models.CASCADE)
    state = models.CharField(max_length=16, default="pending", db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    run_after = models.DateTimeField(default=timezone.now, db_index=True)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(upload__isnull=False, claim__isnull=True, delivery__isnull=True)
                    | models.Q(upload__isnull=True, claim__isnull=False, delivery__isnull=True)
                    | models.Q(upload__isnull=True, claim__isnull=True, delivery__isnull=False)
                ),
                name="job_exactly_one_target",
            )
        ]


class Operation(models.Model):
    claim = models.ForeignKey(Claim, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=40)
    actor = models.CharField(max_length=120)
    reason = models.CharField(max_length=500)
    created_at = models.DateTimeField(default=timezone.now)


class CapacityLock(models.Model):
    """One transaction lock serializes admission against the shared filesystem."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)


class WorkspaceReservation(models.Model):
    """A reservation lives until its particular process workspace is removed."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    directory = models.CharField(max_length=200, unique=True)
    size = models.PositiveIntegerField()
    output_bytes = models.PositiveIntegerField(default=0)
    upload = models.ForeignKey(Upload, null=True, blank=True, on_delete=models.PROTECT)
    claim = models.ForeignKey(Claim, null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

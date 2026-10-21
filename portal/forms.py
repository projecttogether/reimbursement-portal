import re

from django import forms
from stdnum import iban


class ClaimForm(forms.Form):
    full_name = forms.CharField(label="Vorname Nachname", max_length=200)
    email = forms.EmailField(label="E-Mail-Adresse", max_length=254)
    postal_address = forms.CharField(
        label="Anschrift", max_length=1000, widget=forms.Textarea(attrs={"rows": 3})
    )
    iban = forms.CharField(label="IBAN", max_length=60)
    purpose = forms.CharField(
        label="Erläuterung zur Erfüllung satzungsmäßiger Zwecke",
        max_length=3000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    trip_location = forms.CharField(label="Ort der Reise", max_length=200, required=False)
    trip_start = forms.DateField(
        label="Reisebeginn",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
    )
    trip_end = forms.DateField(
        label="Reiseende",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
    )
    declaration = forms.BooleanField(label="Erklärung bestätigen")
    privacy_acknowledged = forms.BooleanField(label="Datenschutzhinweis gelesen")

    def __init__(self, *args, event, **kwargs):
        self.event = event
        super().__init__(*args, **kwargs)
        for name in ["trip_location", "trip_start", "trip_end"]:
            self.fields[name].required = event.travel_required
        for name, field in self.fields.items():
            field.widget.attrs["aria-describedby"] = f"error-{name}"
        self.fields["iban"].widget.attrs.update({"autocomplete": "off", "spellcheck": "false"})
        self.fields["email"].widget.attrs["autocomplete"] = "email"
        self.fields["full_name"].widget.attrs["autocomplete"] = "name"
        self.fields["postal_address"].widget.attrs["autocomplete"] = "street-address"

    def clean_iban(self):
        value = self.cleaned_data["iban"].replace(" ", "").replace("\u00a0", "").upper()
        if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", value) or not iban.is_valid(value):
            raise forms.ValidationError("Bitte gib eine gültige IBAN ein.")
        return value

    def clean(self):
        data = super().clean()
        start, end = data.get("trip_start"), data.get("trip_end")
        if start and end and start > end:
            self.add_error("trip_end", "Das Reiseende darf nicht vor dem Reisebeginn liegen.")
        if bool(start) != bool(end):
            self.add_error("trip_end", "Bitte gib beide Reisedaten an.")
        return data

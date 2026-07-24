# Sample Cases

These illustrate the input JSON schema and exercise different SOP rules.

## Case JSON schema

```json
{
  "subject": {
    "first_name": "John",
    "last_name": "Smith",
    "middle_name": "Robert",
    "dob": "1985-06-15",
    "ssn_last4": "1234",
    "gender": "male",
    "race": "White",
    "address_history": [
      { "state": "UT", "county": "Salt Lake", "from": "2015-01-01", "to": null }
    ],
    "annual_salary": 60000,
    "name_grade": 45
  },
  "client": {
    "client_id": "ACME-01",
    "client_name": "Acme Corp",
    "max_years_misdemeanor": null,
    "max_years_felony": null,
    "felonies_only": false
  },
  "records": [
    {
      "record_id": "REC-001",
      "source": "County Court",
      "source_confirmed": true,
      "charge_description": "Grand Theft",
      "offense_level": "felony",
      "disposition": "convicted",
      "arrest_date": "2020-05-10",
      "file_date": "2020-05-15",
      "disposition_date": "2020-11-20",
      "release_date": "2022-03-15",
      "parole_start_date": "2022-03-15",
      "state": "CA",
      "county": "Los Angeles",
      "court_name": "LA Superior Court",
      "case_number": "BA123456",
      "record_first_name": "John",
      "record_last_name": "Smith",
      "record_middle_name": "R",
      "record_dob": "1985-06-15",
      "record_gender": "male"
    }
  ]
}
```

## Sample files

- `sample_case_report.json` — Clean CA felony conviction, should REPORT
- `sample_case_exclude_dismissed.json` — Dismissed disposition, EXCLUDE
- `sample_case_exclude_petty.json` — Traffic infraction, EXCLUDE
- `sample_case_escalate_missing_disposition.json` — Missing disposition, ESCALATE
- `sample_case_ca_marijuana.json` — Old CA marijuana possession, EXCLUDE
- `sample_case_ma_ban_the_box.json` — MA misdemeanor > 3yr, EXCLUDE
- `sample_case_ny_pending.json` — NY pending charge with salary, REPORT
- `sample_case_common_name.json` — Common-name red flag, ESCALATE
- `sample_case_gender_mismatch.json` — Level 2 disqualifier, EXCLUDE
- `sample_batch.json` — Array of cases for batch mode

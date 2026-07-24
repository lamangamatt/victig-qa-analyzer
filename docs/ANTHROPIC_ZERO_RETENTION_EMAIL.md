# Draft: Anthropic Zero-Retention Request

Send from: matt@victig.com (or your billing-contact email on the Anthropic account)
To: **privacy@anthropic.com** (with cc: **support@anthropic.com**)
Subject: **Zero Data Retention (ZDR) request — VICTIG API account**

---

Hello Anthropic team,

I'm writing to request Zero Data Retention on our API traffic. Some
context:

- **Company:** VICTIG (consumer reporting agency; FCRA-regulated)
- **Anthropic account holder / billing email:** matt@victig.com
- **API use case:** We use Claude to extract structured data from raw
  criminal record and candidate paste text within an internal QA tool.
  The pasted text contains personally identifiable information (name,
  DOB, partial SSN, address history) and criminal record details.
- **Data handling stance:** As an FCRA-regulated CRA, we are required
  to have documented controls over the transmission and retention of
  consumer data at every sub-processor. We can't accept the standard
  30-day retention window.

We'd like the following confirmed in writing:

1. **Zero data retention** on our API traffic — no inputs, outputs, or
   metadata retained beyond the request lifetime, except as strictly
   required by law.
2. **No use for training** (already the default per your API terms, but
   we'd appreciate confirmation).
3. **DPA / BAA availability** if applicable — we'd like a Data
   Processing Addendum on file that reflects the ZDR arrangement.

Happy to jump on a call or complete a form if you have a standard
process. Please let me know what you need from us.

Thank you,
Matt Visser
VICTIG
matt@victig.com
801-598-4813

---

## Notes for Matt (delete before sending)

- Their standard process for ZDR is a light form on their side; they'll
  typically enable it within a couple of business days for legitimate B2B
  use cases.
- If they ask for volume estimates, current usage is very low
  (< 100 requests/day expected initially).
- If you get a DPA back, forward it to Todd Higey to review before
  countersigning.
- Once ZDR is confirmed, note it here + in TOOLS.md so we have a record.

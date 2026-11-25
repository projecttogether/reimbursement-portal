# The product name, the same in every language and deployment.
APP_NAME = "Reimbursement Portal"
# The organization that makes the app: its logo and name on the sign-in page, in the
# sidebar and on the About card (PT's app identity rules: the app first, PT second).
ORGANIZATION = "ProjectTogether"
# The app's source code: the About card and the sign-in page link to it.
REPOSITORY_URL = "https://github.com/projecttogether/reimbursement-portal"
# What the app is for, in one sentence: the large text of the sign-in page.
APP_TAGLINE = "Collect expense claims with receipts and signatures, and send them to accounting."
# Standard participant texts, in English; portal.i18n holds their German version.
DECLARATION = (
    "I confirm that the stated expenses were actually incurred, serve the stated purpose "
    "and have not been reimbursed before."
)
DECLARATION_VERSION = "reimbursement-v1"
MAX_IMAGE_PIXELS = 40_000_000
MAX_NORMALIZED_FILE_BYTES = 10_000_000
MAX_PDF_PAGES = 30
MAX_RECEIPT_DIMENSION = 3508
FORM_TITLE = "Your expenses. Simply submitted."
FORM_DESCRIPTION = (
    "Submit the costs you paid out of your own pocket for reimbursement. "
    "Your details, receipts and signature are sent to accounting as a PDF."
)
PAYMENT_TIMING = (
    "For questions about your claim or the payment, contact the address given in the form."
)

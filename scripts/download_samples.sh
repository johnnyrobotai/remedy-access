#!/usr/bin/env bash
# Populate demo/pdfs/ with the LACCD Student Forms demo inventory.
# Idempotent: skips files that already exist.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOCS="$REPO_ROOT/demo/pdfs"
mkdir -p "$DOCS"

fetch() {
  local out="$1" url="$2"
  if [ -s "$DOCS/$out" ]; then
    echo "skip  $out (already present)"
    return
  fi
  echo "fetch $out  <-  $url"
  curl -fL --retry 3 --retry-delay 1 \
    -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36" \
    -e "https://www.laccd.edu/students/student-forms" \
    -o "$DOCS/$out" "$url" || {
      echo "  failed; you can add $out manually"
      rm -f "$DOCS/$out"
    }
}

fetch Nonresident_Tuition_Exemption_Request.pdf "https://www.laccd.edu/sites/laccd.edu/files/2022-08/Nonresident_Tuition_Exemption_Request.pdf"
fetch Nonresident_Tuition_Fee_Waiver_Application.docx "https://www.laccd.edu/sites/laccd.edu/files/2024-08/Nonresident%20Tuition%20Fee%20Waiver%20Application.docx"
fetch Nonresident_Tuition_Fee_Waiver_Application.pdf "https://www.laccd.edu/sites/laccd.edu/files/2024-08/Nonresident%20Tuition%20Fee%20Waiver%20Application.pdf"
fetch Supplemental_Residency_Questionnaire.pdf "https://www.laccd.edu/sites/laccd.edu/files/2022-08/Supplemental_Residency_Questionnaire.pdf"
fetch Certification_of_Homeless_Status_REV_012018.docx "https://www.laccd.edu/sites/laccd.edu/files/2022-08/Certification%20of%20Homeless%20Status%20REV%20012018.docx"
fetch Certification_of_Homeless_Status_REV_012018.pdf "https://www.laccd.edu/sites/laccd.edu/files/2022-08/Certification%20of%20Homeless%20Status%20REV%20012018.pdf"
fetch APPLICATION_FOR_NONCREDIT_ADMISSION_2.6_Fillable.pdf "https://www.laccd.edu/sites/laccd.edu/files/2022-08/APPLICATION%20FOR%20NONCREDIT%20ADMISSION%202.6%20Fillable.pdf"
fetch APPLICATION_FOR_NONCREDIT_ADMISSION_2.7_Fillable_SPANISH.pdf "https://www.laccd.edu/sites/laccd.edu/files/2022-08/APPLICATION%20FOR%20NONCREDIT%20ADMISSION%202.7%20Fillable%20SPANISH.pdf"
fetch pass_no_pass_petition.pdf "https://www.laccd.edu/sites/laccd.edu/files/2023-10/pass_no_pass_petition.pdf"
fetch High_School_Graduation_Update_Form.pdf "https://www.laccd.edu/sites/laccd.edu/files/2022-08/High%20School%20Graduation%20Update%20Form.pdf"
fetch laccd_ew_petition_240209_0.pdf "https://www.laccd.edu/sites/laccd.edu/files/2024-05/laccd_ew_petition_240209_0.pdf"
fetch K-12_Parent_Consent.pdf "https://www.laccd.edu/sites/laccd.edu/files/2026-02/K-12%20Parent%20Consent.pdf"
fetch LACCD_Petition_for_Academic_Renewal_250505.pdf "https://www.laccd.edu/sites/laccd.edu/files/2025-09/LACCD%20Petition%20for%20Academic%20Renewal%20250505.pdf"
fetch Petition_for_Credit_for_Prior_Learning_v7.pdf "https://www.laccd.edu/sites/laccd.edu/files/2024-07/Petition%20for%20Credit%20for%20Prior%20Learning%20v7.pdf"

echo
echo "Done. demo/pdfs/ LACCD contents:"
ls -1 "$DOCS" | sed 's/^/  /'

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from woob.capabilities.payment import (
    OneTimePayment,
    OneTimePaymentType,
    PaymentValidationError,
    PaymentValidationErrorCode,
)
from woob.exceptions import BrowserUnavailable
from woob.tools.capabilities.bank.iban import get_iban_bank_code
from woob.tools.json import json
from woob_modules.stet.payment import (
    RedirectFlow,
    Stet141PaymentBrowser,
    Stet141PaymentDialect,
    StetException,
)

if TYPE_CHECKING:
    from requests.models import PreparedRequest, Request


CRAGR_BANK_CODES = {
    "www.ca-alpesprovence.fr": "11306",
    "www.ca-alsace-vosges.fr": "17206",
    "www.ca-anjou-maine.fr": "17906",
    "www.ca-aquitaine.fr": "13306",
    "www.ca-atlantique-vendee.fr": "14706",
    "www.ca-briepicardie.fr": "18706",
    "www.ca-cb.fr": "11006",
    "www.ca-centrefrance.fr": "16806",
    "www.ca-centreloire.fr": "14806",
    "www.ca-centreouest.fr": "19506",
    "www.ca-centrest.fr": "17806",
    "www.ca-charente-perigord.fr": "12406",
    "www.ca-cmds.fr": "11706",
    "www.ca-corse.fr": "12006",
    "www.ca-cotesdarmor.fr": "12206",
    "www.ca-des-savoie.fr": "18106",
    "www.ca-finistere.fr": "12906",
    "www.ca-franchecomte.fr": "12506",
    "www.ca-guadeloupe.fr": "14006",
    "www.ca-illeetvilaine.fr": "13606",
    "www.ca-languedoc.fr": "13506",
    "www.ca-loirehauteloire.fr": "14506",
    "www.ca-lorraine.fr": "16106",
    "www.ca-martinique.fr": "19806",
    "www.ca-morbihan.fr": "16006",
    "www.ca-nmp.fr": "11206",
    "www.ca-nord-est.fr": "10206",
    "www.ca-norddefrance.fr": "16706",
    "www.ca-normandie-seine.fr": "18306",
    "www.ca-normandie.fr": "16606",
    "www.ca-paris.fr": "18206",
    "www.ca-pca.fr": "19106",
    "www.ca-reunion.fr": "19906",
    "www.ca-sudmed.fr": "17106",
    "www.ca-sudrhonealpes.fr": "13906",
    "www.ca-toulouse31.fr": "13106",
    "www.ca-tourainepoitou.fr": "19406",
    "www.ca-valdefrance.fr": "14406",
    "www.ca-pyrenees-gascogne.fr": "16906",
}


class CreditAgricoleStetPaymentDialect(Stet141PaymentDialect):
    """Credit Agricole Stet custom payment dialect.

    Credit Agricole does not support sending SICT transfers between its own accounts.
    When we detect a beneficiary from CA, we use SCT instead.
    """

    website: str

    def build_payment_type_information(self, payment: OneTimePayment) -> dict:
        data = super().build_payment_type_information(payment)

        if payment.get_type() != OneTimePaymentType.SCT_INST:
            return data

        # Check if the payment is between accounts of the same bank
        payer_bank_code = CRAGR_BANK_CODES.get(self.website)
        if payer_bank_code is None:
            self.logger.info(
                "Unknown Credit Agricole website: %s, cannot determine bank code.",
                self.website,
            )
            return data

        benef_bank_codes = [
            get_iban_bank_code(inst.beneficiary.iban) for inst in payment.instructions
        ]

        # It's either a single payment or a bulk payment with all beneficiaries in Credit Agricole.
        if set(benef_bank_codes) == {payer_bank_code}:
            self.logger.info("Switching from SICT to SCT for internal Credit Agricole payment.")
            del data["localInstrument"]

        # Bad case: a bulk payment where only some beneficiaries are in Credit Agricole.
        elif payer_bank_code in benef_bank_codes:
            message = (
                "Instant bulk payment is impossible because the beneficiaries contain some "
                "accounts belonging to the same Credit Agricole branch."
            )
            self.logger.info(message)
            raise PaymentValidationError(
                message,
                code=PaymentValidationErrorCode.INVALID_BENEFICIARY,
            )

        return data


class CreditAgricoleStetPaymentBrowser(Stet141PaymentBrowser):
    BASEURL = "https://psd2-api.{self.region}.fr/dsp2/v1/"
    OAUTH_TOKEN_URL = "https://psd2-api.{self.region}.fr/authentication/v1/openid/token"

    VALIDATION_REDIRECT_FLOW = RedirectFlow.SIMPLE_WITHOUT_CONFIRMATION

    DIALECT = CreditAgricoleStetPaymentDialect

    # Requesting an 'application/hal+json' body fails with an HTTP 406 error.
    ACCEPT_HAL_JSON = False

    oauth_token_page = Stet141PaymentBrowser.oauth_token_page.with_headers(
        {
            # Mandatory headers, cats support says it will be removed
            # for 2019-09-14; still needed in August 2023...
            "cats_consommateur": json.dumps(
                {
                    "consommateur": {"nom": "ING", "version": "1.0.0"},
                }
            ),
            "cats_consommateurorigine": json.dumps(
                {
                    "consommateur": {"nom": "ING", "version": "1.0.0"},
                }
            ),
            "cats_canal": json.dumps(
                {
                    "canal": {"canalId": "internet", "canalDistribution": "internet"},
                }
            ),
        }
    )

    _response_code_403_error_mapping: dict[str, PaymentValidationErrorCode] = {
        # For Instant Payment, a 403 error can occur if the beneficiary is invalid.
        "instant payment unauthorized": PaymentValidationErrorCode.INVALID_BENEFICIARY,
        "exceed the amount limit": PaymentValidationErrorCode.INVALID_AMOUNT,
    }

    @property
    def dialect(self) -> CreditAgricoleStetPaymentDialect:
        dialect: CreditAgricoleStetPaymentDialect = super().dialect
        dialect.website = self.config["website"].get()
        return dialect

    def setup_session(self):
        """Set up the initialized session."""
        website = self.config["website"].get()
        self.region = website.replace("www.", "").replace(".fr", "")

        website_config = self.config["website"]
        # Chalus redefines the website choice to a single value
        if website_config.choices is not None:
            self.region_formatted_name = website_config.choices[website]
        else:
            self.region_formatted_name = website_config.get()

    def prepare_request(self, request: Request) -> PreparedRequest:
        # Add a proprietary correlation header.
        request.headers["correlationid"] = str(uuid4())
        return super().prepare_request(request)

    def raise_for_status_specific(self, response):
        if response.status_code == 403:
            msg = response.json().get("message", "").casefold()
            if "exceed the amount limit" in msg:
                raise PaymentValidationError(msg, code=PaymentValidationErrorCode.INVALID_AMOUNT)
            # This error can appear when the beneficiary bank does not support instant payments
            if "instant payment unauthorized" in msg or "invalid data creditor iban" in msg:
                raise PaymentValidationError(
                    msg, code=PaymentValidationErrorCode.INVALID_BENEFICIARY
                )
            # This error may appear temporarily if the bank can't check that instant payments are enabled
            if "instant payment not activated for this bank" in msg:
                website = self.config["website"]
                raise BrowserUnavailable(
                    f"Instant payment is temporarily unavailable on Credit Agricole {website}."
                )
            # This error can appear if the payer chooses the wrong website
            if "invalid data debtor iban" in msg:
                raise PaymentValidationError(
                    "IBAN émetteur invalide, vérifiez l'iban fourni ainsi "
                    + "que la caisse du crédit agricole choisie: "
                    + self.region_formatted_name,
                    code=PaymentValidationErrorCode.INVALID_PAYER,
                )
            # Answer of the bank for this error:
            # Il n’est pas possible de réaliser un virement IP au sein du même
            # établissement. L’établissement bancaire du donneur d’ordre et du
            # bénéficiaire doivent être différents (c’est une spécificité CA)
            if "instant payment is not allowed within the same bank" in msg:
                raise PaymentValidationError(
                    msg, code=PaymentValidationErrorCode.UNREACHABLE_BENEFICIARY
                )

    def create_and_validate_payment(self) -> None:
        try:
            super().create_and_validate_payment()
        except StetException as exc:
            details = exc.detail.casefold()

            if exc.response.status_code == 403:
                for error_key, code in self._response_code_403_error_mapping.items():
                    if error_key in details:
                        raise PaymentValidationError(
                            code=code,
                        ) from exc

            raise

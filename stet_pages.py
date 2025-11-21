import datetime
import json
import re
from decimal import Decimal
from urllib.parse import urlparse

from dateutil.tz import tzutc
from six import string_types

from woob.browser.elements import DictElement, ItemElement, method
from woob.browser.filters.json import Dict
from woob.browser.filters.standard import (
    CleanDecimal,
    CleanText,
    Coalesce,
    Currency,
    Date,
    DateTime,
    Env,
    Field,
    Lower,
    Map,
    MapIn,
    Regexp,
    Type,
)
from woob.browser.pages import JsonPage, pagination
from woob.capabilities.bank import (
    Account,
    AccountIdentification,
    AccountOwnerType,
    AccountParty,
    AccountSchemeName,
    Balance,
    BankTransactionCode,
    PartyIdentity,
    Recipient,
    Transaction,
    TransactionCounterparty,
    TransferDateType,
    TransferStatus,
)
from woob.capabilities.bank.transfer import (
    TransferBankError,
    TransferCancelledByUser,
    TransferError,
    TransferInvalidAmount,
    TransferInvalidDate,
    TransferInvalidEmitter,
    TransferInvalidRecipient,
)
from woob.capabilities.base import NotAvailable, empty
from woob.capabilities.profile import Person
from woob.tools.capabilities.bank.iban import is_iban_valid
from woob.tools.capabilities.bank.stet import (
    StetTransfer,
    StetTransferFrequency,
    TransferCancelledForRegulatoryReason,
    TransferCancelledWithNoReason,
    TransferNotValidated,
)
from woob.tools.capabilities.bank.transaction_codes import (
    BANK_TRANSACTION_CODES,
    FRENCH_BANK_TRANSACTION_CODES,
)
from woob.tools.capabilities.bank.transactions import parse_with_patterns

ACCOUNT_TYPES = {
    "CACC": Account.TYPE_CHECKING,
    "CARD": Account.TYPE_CARD,
}

# Unfortunately Stet defines only PRIV (private) and ORGA (pro)
USAGE_TYPES = {
    "PRIV": AccountOwnerType.PRIVATE,
    "ORGA": AccountOwnerType.ORGANIZATION,
}

ACCOUNT_SCHEME_NAME = {
    "BANK": AccountSchemeName.BANK_PARTY_IDENTIFICATION,
    "CPAN": AccountSchemeName.CPAN,
    "MPAN": AccountSchemeName.MPAN,
    "TPAN": AccountSchemeName.TPAN,
    "BBAN": AccountSchemeName.BBAN,
    "IBAN": AccountSchemeName.IBAN,
}

PARTY_ROLE = {
    "unknown": PartyIdentity.ROLE_UNKNOWN,
    "account holder": PartyIdentity.ROLE_HOLDER,
    "account co-holder": PartyIdentity.ROLE_CO_HOLDER,
    "holder": PartyIdentity.ROLE_HOLDER,
    "attorney": PartyIdentity.ROLE_ATTORNEY,
    "custodian for minor": PartyIdentity.ROLE_CUSTODIAN_FOR_MINOR,
    "legal guardian": PartyIdentity.ROLE_LEGAL_GUARDIAN,
    "nominee": PartyIdentity.ROLE_NOMINEE,
    "successor on death": PartyIdentity.ROLE_SUCCESSOR_ON_DEATH,
    "trustee": PartyIdentity.ROLE_TRUSTEE,
    "mandate": PartyIdentity.ROLE_ATTORNEY,
}


class RevokePage(JsonPage):
    pass


class AccountsPage(JsonPage):
    def build_doc(self, content):
        if self.response.status_code == 204:
            # Stet allows 204 response (no content)
            self.logger.info("JSON has no content")
            return {"accounts": []}

        return super(AccountsPage, self).build_doc(content)

    @method
    class iter_accounts(DictElement):
        item_xpath = "accounts"

        class item(ItemElement):
            klass = Account

            def condition(self):
                # Dict('details') returns None if related value is null in the JSON
                return not any(
                    skippable
                    for skippable in (
                        "débit immédiat",
                        "immediat_debit",
                    )
                    if skippable in (Dict("details", default="")(self) or "")
                )

            obj_id = Dict("resourceId")
            obj_label = Dict("name")
            obj_number = Dict("accountId/iban", default=NotAvailable)
            obj_bic = Dict("bicFi", default=NotAvailable)

            # Currency can be in different locations
            # see §4.2.5.1 https://www.stet.eu/assets/files/PSD2/1-4-1/api-dsp2-stet-v1.4.1.3-part-2-functional-model.pdf
            obj_currency = Currency(
                Coalesce(
                    Dict("balances/0/balanceAmount/currency", default=None),
                    Dict("accountId/currency", default=None),
                    default="",
                ),
                default=NotAvailable,
            )

            obj_iban = Dict("accountId/iban", default=NotAvailable)
            obj_type = MapIn(
                Dict("cashAccountType", default=""), ACCOUNT_TYPES, Account.TYPE_CHECKING
            )
            obj_owner_type = MapIn(
                Dict("usage", default=""), USAGE_TYPES, NotAvailable
            )  # Not mandatory

            def obj_balance(self):
                if Field("type")(self) == Account.TYPE_CARD:
                    balance = Decimal("0.00")
                else:
                    balance = Dict("balances/0/balanceAmount/amount", default=None)  # noqa: no self needed
                    balance = CleanDecimal(balance, default=NotAvailable)(self)
                return balance

            class obj_party(ItemElement):
                klass = AccountParty

                def condition(self):
                    return self.page.browser.with_account_party

                class obj_party_identities(DictElement):
                    # Need to define find_elements too, and to return
                    # a list, as party_identities is a field of list type
                    def find_elements(self):
                        return [self.el]

                    class item(ItemElement):
                        klass = PartyIdentity

                        def obj_role(self):
                            # Sometimes, the key is present in the json response
                            # but its value is null and this cause an error with
                            # the Lower filter. Because of this, we first try to
                            # get the key and then check its value before mapping it.
                            psu_status = Dict("psuStatus", default="")(self)

                            if psu_status:
                                return MapIn(Lower(Dict("psuStatus")), PARTY_ROLE)(self)

                            return PartyIdentity.ROLE_UNKNOWN

                class obj_account_identifications(DictElement):
                    # Need to define find_elements too, and to return
                    # a list, as account_identifications is a field of list type
                    def find_elements(self):
                        self.env["other_identification"] = None
                        return [self.el["accountId"]]

                    class item(ItemElement):
                        klass = AccountIdentification

                        def obj_scheme_name(self):
                            scheme_names = self.el.keys()

                            if "iban" in scheme_names:
                                return AccountSchemeName.IBAN

                            elif "other" in scheme_names:
                                other_scheme_name = Dict("other/schemeName", default="")(self)
                                mapped_scheme_name = None

                                if other_scheme_name:
                                    mapped_scheme_name = MapIn(
                                        Dict("other/schemeName"), ACCOUNT_SCHEME_NAME, None
                                    )(self)

                                if not mapped_scheme_name:
                                    raise AssertionError(
                                        f"Unhandled scheme type: {other_scheme_name}"
                                    )
                                else:
                                    self.env["other_identification"] = Dict("other/identification")(
                                        self
                                    )
                                    return mapped_scheme_name

                        def obj_identification(self):
                            if Field("scheme_name")(self) == AccountSchemeName.IBAN:
                                return Dict("iban")(self)

                            return Env("other_identification")(self)


class BalancesPage(JsonPage):
    BALANCE_TYPES = {
        # To fill in children
    }

    def fill_account(self, account):
        # 1 - XPCD : Expected / Instant balance at the time of the request
        # 2 - CLBD : Account balance at a point of time in the past
        # 3 - OTHR : Any other kind of balance, without more information
        balances_available = {
            balance["balanceType"]: balance["balanceAmount"]["amount"]
            for balance in self.doc["balances"]
        }
        for balance_priority in ("XPCD", "CLBD", "OTHR"):
            if balance_priority in balances_available:
                account.balance = Decimal(
                    "".join(str(balances_available.get(balance_priority)).split())
                )
                return

    @method
    class fill_balances(ItemElement):
        class obj_all_balances(DictElement):
            item_xpath = "balances"

            class item(ItemElement):
                klass = Balance

                def condition(self):
                    balance_type = Dict("balanceType")(self)
                    is_balance_type_handled = balance_type in self.page.BALANCE_TYPES

                    if not is_balance_type_handled:
                        self.logger.warning("Unknown balance type: %s", balance_type)

                    return is_balance_type_handled

                def obj_type(self):
                    return MapIn(Dict("balanceType"), self.page.BALANCE_TYPES)(self)

                def obj_credit_included(self):
                    balance_type = Dict("balanceType")(self)
                    return balance_type == "ITAV"

                obj_label = Dict("name")
                obj_amount = CleanDecimal.SI(Dict("balanceAmount/amount"))
                obj_currency = Currency(Dict("balanceAmount/currency"))

                obj_last_update = DateTime(
                    CleanText(
                        Type(
                            Dict("lastChangeDateTime", default=""),
                            type=str,  # Checking type to prevent NoneType returned by APIs
                            default="",
                        )
                    ),
                    default=NotAvailable,
                )

                obj_reference_date = Date(
                    CleanText(
                        Type(
                            Dict("referenceDate", default=""),
                            type=str,  # Checking type to prevent NoneType returned by APIs
                            default="",
                        )
                    ),
                    default=NotAvailable,
                )


class TransactionsPage(JsonPage):
    def build_doc(self, content):
        # API can return no content
        if not content:
            self.logger.info("JSON has no content")
            return {"transactions": []}
        if (
            self.browser.AUDIT_TRANSACTION_CODE
            and not self.browser.transaction_code_logger_triggered
        ):
            # Check if bankTransactionCode is implemented
            if "bankTransactionCode" in content:
                self.browser.transaction_code_logger_triggered = (
                    True  # set to true to avoid flooding logger
                )
                self.logger.info("Bank transaction code found")
        if (
            self.browser.AUDIT_TRANSACTIONS_MERCHANT_ID
            and not self.browser.transaction_merchant_id_logger_triggered
        ):
            if "merchantID" in content:
                self.browser.transaction_merchant_id_logger_triggered = (
                    True  # set to true to avoid flooding logger
                )
                self.logger.info("Transaction's merchantID found")

        # API can return content with no transactions key (example for cards)
        # It's the case on creditdunord_stet and bred_stet modules
        if "transactions" not in json.loads(content):
            self.logger.info("transactions key not found")
            return {"transactions": []}
        return super(TransactionsPage, self).build_doc(content)

    @method
    class iter_transactions(DictElement):
        item_xpath = "transactions"

        class item(ItemElement):
            klass = Transaction

            obj_id = NotAvailable  # Ids given by many banks are not reliable.
            obj_date = Coalesce(
                Date(Dict("bookingDate", default=""), default=None),
                Date(Dict("expectedBookingDate", default=""), default=None),
            )
            obj__status = Dict("status", default=NotAvailable)

            def obj_raw(self):
                # XXX this part of code should/will be reworked soon
                info = Dict("remittanceInformation")(self)

                if isinstance(info, string_types):  # Handle cases where it is not an array
                    if hasattr(self.klass, "Raw"):
                        raw = self.klass.Raw(Dict("remittanceInformation"))(self)
                    else:
                        raw = info
                else:
                    if isinstance(info, list):  # Handle case where the info is here
                        info = [el for el in info if el]  # some items of 'info' may be None
                        raw = CleanText().filter(" ".join(info))
                    else:
                        # Nominal case follows STET specification
                        raw = CleanText().filter(
                            " ".join(
                                Coalesce(
                                    Dict("structured", default=[]),
                                    Dict("unstructured", default=[]),
                                    default=[],
                                )(info)
                            )
                        )

                    if hasattr(self.klass, "PATTERNS"):
                        # with python3 the field `date` from the obj transaction
                        # could be not loaded yet so we force it here.
                        if not self.obj.date:
                            self.obj.date = Field("date")(self)
                        parse_with_patterns(raw, self.obj, self.klass.PATTERNS)

                return raw

            def obj_amount(self):
                signs = {"CRDT": 1, "DBIT": -1}
                sign = signs[Dict("creditDebitIndicator")(self)]
                return sign * abs(CleanDecimal(Dict("transactionAmount/amount"))(self))

            class obj_bank_transaction_code(ItemElement):
                klass = BankTransactionCode

                def _set_bank_transaction_code_attributes(
                    self,
                    domain=NotAvailable,
                    family=NotAvailable,
                    sub_family=NotAvailable,
                ):
                    self.env["domain"] = domain
                    self.env["family"] = family
                    self.env["sub_family"] = sub_family

                def parse(self, el):
                    # A verification is made with the use of BANK_TRANSACTION_CODES and
                    # FRENCH_BANK_TRANSACTION_CODES to check if the bank's data follows
                    # ISO20022 or CFONB standard. If the data is correct we set the
                    # bank transaction codes attributes to the provided value, else NotAvailable

                    sub_family = Dict("bankTransactionCode/subFamily", default=NotAvailable)(self)

                    if self.page.browser.ISO_20022_TRANSACTION_CODE:
                        domain = Dict("bankTransactionCode/domain", default=NotAvailable)(self)
                        family = Dict("bankTransactionCode/family", default=NotAvailable)(self)

                        if domain and domain not in BANK_TRANSACTION_CODES:
                            self._set_bank_transaction_code_attributes()
                            self.logger.warning("Unknown bank transaction code domain %s" % domain)

                        elif family and family not in BANK_TRANSACTION_CODES.get(domain, {}):
                            self._set_bank_transaction_code_attributes()
                            self.logger.warning("Unknown bank transaction code family %s" % family)

                        elif sub_family and sub_family not in BANK_TRANSACTION_CODES.get(
                            domain, {}
                        ).get(family, []):
                            self._set_bank_transaction_code_attributes()
                            self.logger.warning(
                                "Unknown bank transaction code subFamily %s" % sub_family
                            )

                        else:
                            self._set_bank_transaction_code_attributes(domain, family, sub_family)

                    else:
                        if sub_family and sub_family in FRENCH_BANK_TRANSACTION_CODES:
                            domain, family, sub_family = FRENCH_BANK_TRANSACTION_CODES[sub_family]
                            self._set_bank_transaction_code_attributes(domain, family, sub_family)

                    super().parse(el)

                obj_domain = Env("domain", default=NotAvailable)
                obj_family = Env("family", default=NotAvailable)
                obj_sub_family = Env("sub_family", default=NotAvailable)

                def validate(self, obj):
                    return any(
                        bank_transaction_code_attr
                        for bank_transaction_code_attr in (obj.domain, obj.family, obj.sub_family)
                    )

            class obj_counterparty(ItemElement):
                klass = TransactionCounterparty

                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.env["credit_or_debit"] = Dict(
                        "creditDebitIndicator", default=NotAvailable
                    )(self)

                def _should_use_debtor(self):
                    return Env("credit_or_debit")(self) == "CRDT"

                def _should_use_creditor(self):
                    return Env("credit_or_debit")(self) == "DBIT"

                def condition(self):
                    if Dict("relatedParties", default=None)(self):
                        if self._should_use_debtor() or self._should_use_creditor():
                            return True

                        self.logger.warning(
                            "relatedParties info present without creditor/debtor information"
                        )

                    return False

                def obj_label(self):
                    if self._should_use_debtor():
                        return CleanText(
                            Dict("relatedParties/debtor/name", default=None), default=NotAvailable
                        )(self)
                    return CleanText(
                        Dict("relatedParties/creditor/name", default=None), default=NotAvailable
                    )(self)

                def obj_account_scheme_name(self):
                    if self._should_use_debtor():
                        debtor_creditor = "debtorAccount"
                    else:
                        debtor_creditor = "creditorAccount"

                    debtor_creditor_raw = Dict(f"relatedParties/{debtor_creditor}", default={})(
                        self
                    )
                    debtor_creditor_uppercase = dict(
                        (k.upper(), v) for k, v in debtor_creditor_raw.items()
                    )

                    account_scheme_name = MapIn(
                        debtor_creditor_uppercase,
                        ACCOUNT_SCHEME_NAME,
                        NotAvailable,
                    )(self)

                    if debtor_creditor_raw and not account_scheme_name:
                        self.logger.warning(
                            "AccountSchemeName different from IBAN and BBAN: %s",
                            Dict("relatedParties")(self),
                        )

                    return account_scheme_name

                def _retrieve_account_identification(self, debtor_creditor):
                    acc_identification = None
                    if Field("account_scheme_name")(self) == "iban":
                        acc_identification = CleanText(
                            Dict(f"{debtor_creditor}/iban", default=None), default=NotAvailable
                        )(self)
                    elif Field("account_scheme_name")(self) == "bban":
                        acc_identification = CleanText(
                            Dict(f"{debtor_creditor}/bban", default=None), default=NotAvailable
                        )(self)

                    if Field("account_scheme_name")(self) and not acc_identification:
                        # Not sure that this case occurs on Stet
                        self.logger.warning("Account identification is an empty string.")

                    return acc_identification or NotAvailable

                def obj_account_identification(self):
                    if self._should_use_debtor():
                        return self._retrieve_account_identification("relatedParties/debtorAccount")
                    elif self._should_use_creditor():
                        return self._retrieve_account_identification(
                            "relatedParties/creditorAccount"
                        )

                def obj_debtor(self):
                    return self._should_use_debtor()


class IdentityLegacyPage(JsonPage):
    @method
    class get_profile(ItemElement):
        """Scraping of end-user identity up to STET v1.4.2
        See https://www.stet.eu/assets/files/PSD2/1-4-2/api-dsp2-stet-v1.4.2.17-part-2-functional-model.pdf part 4.6.
        """

        klass = Person

        obj_name = Dict("connectedPsu")  # Mandatory

        obj__title = Dict("connectedPsuNamePrefix", default=NotAvailable)  # Optional
        obj_firstname = Dict("connectedPsuFirstName", default=NotAvailable)  # Optional
        obj_lastname = Dict("connectedPsuLastName", default=NotAvailable)  # Optional


class IdentityPage(JsonPage):
    @method
    class get_profile(ItemElement):
        """Scraping of end-user identity starting from STET v1.5.0
        See https://www.stet.eu/assets/files/PSD2/1-5/api-dsp2-stet-v1.5.0.43-part-2-functional-model.pdf part 4.8.
        """

        item_xpath = "identity"

        klass = Person

        obj_firstname = Dict("firstName", default=NotAvailable)  # ex: "ELIZABETH"
        obj_lastname = Dict("lastName", default=NotAvailable)  # ex: "HOLMES"

        def obj_name(self):
            # to avoid showing the civility included in "fullName" field,
            # we first check if the first_name and last_name are set, if so
            # we return their concatenation to build the full name
            first_name = Field("firstname")(self)
            last_name = Field("lastname")(self)

            if first_name and last_name:
                return f"{first_name} {last_name}"

            return Dict("fullName")(self)  # Only mandatory field, ex: "MME ELIZABETH HOLMES"

        """
        TODO: use in future implementations as civility:
            Key: 'namePrefix'
            Values possible: 'MADM', 'MISS', 'MIST', 'DOCT'
        """


class OwnersPage(JsonPage):
    # Default document to return when no identities are found
    default_empty_doc = {"identities": [], "company": {}}

    def build_doc(self, content):
        """Build a JSON document from the provided content."""
        if "identities" in content or "company" in content:
            return super().build_doc(content)

        self.logger.warning(
            "Account Parties: owners page with no identities or company, please check the content"
        )
        return self.default_empty_doc

    @method
    class iter_account_party_identities(DictElement):
        item_xpath = "identities"

        class item(ItemElement):
            klass = PartyIdentity

            def obj_full_name(self):
                """Retrieve the full name of the object, constructed from
                first and last names if available, or otherwise use the full
                name with potential cleaning of a name prefix.
                """
                last_name = CleanText(Dict("lastName", default=""), default=NotAvailable)(self)
                first_name = CleanText(Dict("firstName", default=""), default=NotAvailable)(self)

                if last_name and first_name:
                    return f"{first_name} {last_name}"

                name_prefix = CleanText(Dict("namePrefix", default=""))(self)
                full_name = CleanText(Dict("fullName"), default=NotAvailable)(self)

                if name_prefix and full_name:
                    # Clean the name prefix from the full name to be coherent with
                    # the full name retrieved from the profile endpoint.
                    return full_name.replace(name_prefix, "").strip()
                return full_name

    @method
    class get_account_party_company(ItemElement):
        """Retrieve the company name of the account party."""

        item_xpath = "company"

        def condition(self):
            return Dict("identification", default=False)(self)

        klass = PartyIdentity

        obj_full_name = Dict("identification", default=NotAvailable)
        obj_role = PartyIdentity.ROLE_HOLDER


class RecipientsPage(JsonPage):
    # Based on Stet 1.4.2:
    # https://www.stet.eu/assets/files/PSD2/1-4-2/api-dsp2-stet-v1.4.2.17-part-2-functional-model.pdf

    def build_doc(self, content):
        no_benef = {"beneficiaries": []}

        # API can return no content
        if not content:
            self.logger.info("JSON has no content")
            return no_benef

        doc = super(RecipientsPage, self).build_doc(content)

        # If there is no beneficiary associated to the account, the key does not appear in the doc
        if "beneficiaries" not in doc:
            return no_benef

        return doc

    @pagination
    @method
    class iter_recipients(DictElement):
        item_xpath = "beneficiaries"
        # Some banks allow to have multiple recipient with the same iban
        ignore_duplicate = True

        def next_page(self):
            links = self.page.doc.get("_links", {})
            links = {
                k: v for k, v in links.items() if v
            }  # in case key exists with an explicit None value
            self_link = links.get("self", {}).get("href", {})
            next_link = links.get("next", {}).get("href", {})
            if self_link and next_link and self_link != next_link:
                return self.page.absurl("?%s" % urlparse(next_link).query)

        class item(ItemElement):
            klass = Recipient

            obj_iban = Dict("creditorAccount/iban", default=NotAvailable)
            obj_id = Coalesce(
                Field("iban"),
                Dict("id", default=NotAvailable),
                # No `default=NotAvailable` to let the module crash since 'id' is needed in backend
            )
            obj_bank_name = Coalesce(
                Dict("creditorAgent/name", default=NotAvailable),
                Field("_bic_fi"),
                default=NotAvailable,
            )
            obj_category = "Externe"  # There are no internal recipients in Stet 1.4
            obj_enabled_at = datetime.date.today()

            # Not in Recipient model
            obj__bic_fi = Dict("creditorAgent/bicFi", default=NotAvailable)

            def obj_label(self):
                # 'name' is the only mandatory field in Stet 1.4
                # but on some APIs we still don't have it so we have to workaround
                label = Dict("creditor/name", default="")(self) or Field("iban")(self)
                assert label, "Creditor name and IBAN are empty"
                return label

            def validate(self, obj):
                if obj.iban:
                    return is_iban_valid(obj.iban)
                return True


class PaymentRequestPage(JsonPage):
    # See `get_transfer_rejected_reason()` for code definition
    MAPPING_REASONS = {
        "AC01": TransferInvalidEmitter(
            message="Le numéro du compte émetteur est invalide ou non-existant."
        ),
        "AC04": TransferInvalidEmitter(
            message="Le compte émetteur a été cloturé et ne peut être utilisé."
        ),
        "AC06": TransferInvalidEmitter(
            message="Le compte émetteur est bloqué et ne peut être utilisé."
        ),
        "AG01": TransferInvalidEmitter(
            message="Ce type de virement est impossible sur le compte émetteur."
        ),
        "AM02": TransferInvalidAmount(
            message="Le montant du virement est supérieur au plafond maximum sur cette banque."
        ),
        "AM04": TransferInvalidAmount(
            message="Fonds insuffisants sur le compte émetteur pour ce virement."
        ),
        "AM18": AssertionError("Something went wrong during transfer: InvalidNumberOfTransactions"),
        "CH03": TransferInvalidDate(),
        "CUST": TransferCancelledByUser(
            message="Le virement a été annulé par l'émetteur du virement."
        ),
        "DS02": TransferCancelledByUser(
            message="Le virement a été annulé par une personne autorisée."
        ),
        "DUPL": TransferCancelledForRegulatoryReason(
            message="Le virement a été considéré comme étant un doublon par la banque émettrice."
        ),
        "FF01": TransferError("Something went wrong during transfer: InvalidFileFormat"),
        "FRAD": TransferCancelledForRegulatoryReason(
            message="Le virement a été considéré comme potentiellement frauduleux par la banque émettrice."
        ),
        "MS03": TransferCancelledWithNoReason(
            message="Le virement a été annulé par la banque émettrice qui n'a pas fourni de raison spécifique."
        ),
        "NOAS": TransferNotValidated(
            message="La demande d'autorisation du virement a expiré, l'émetteur ne s'est pas authentifié ou n'a pas validé la demande du virement."
        ),
        "RR01": TransferInvalidEmitter(
            message="Les informations du compte ou d'identification de l'émetteur sont insuffisantes ou manquantes."
        ),
        "RR03": TransferInvalidRecipient(),
        "RR04": TransferCancelledForRegulatoryReason(
            message="Le virement a été annulé par la banque émettrice pour des raisons réglementaires."
        ),
        "RR12": TransferCancelledForRegulatoryReason(message="InvalidPartyId RR12"),
        "TECH": TransferCancelledWithNoReason(
            message="Le virement a été rejeté pour raison technique par la banque émettrice (raison TECH)."
        ),
    }

    def decode_transfer_rejected_reason(self, stet_status_reason):
        """Get an exception according to the given rejected reason.

        Note that this method must only be called if the STET payment
        information status is 'RJCT' or 'CANC'.

        Only the following values are allowed:

        AC01 (IncorectAccountNumber)
            The account number is either invalid or does not exist

        AC04 (ClosedAccountNumber)
            The account is closed and cannot be used.

        AC06 (BlockedAccount)
            The account is blocked and cannot be used.

        AG01 (Transaction forbidden)
            Transaction forbidden on this type of account.

        AM18 (InvalidNumberOfTransactions)
            The number of transactions exceeds the ASPSP acceptance limit.

        CH03 (RequestedExecutionDateOrRequestedCollectionDateTooFarInFuture)
            The requested execution date is too far in the future.

        CUST (RequestedByCustomer)
            The reject is due to the debtor: refusal or lack of liquidity.

        DS02 (OrderCancelled)
            An authorized user has cancelled the order.

        DUPL (DuplicatePayment)
            Payment is a duplicate of another payment. Can only be set by a
            PISP for a payment request cancellation.

        FF01 (InvalidFileFormat)
            The reject is due to the original Payment Request which is
            invalid (syntax, structure or values).

        FRAD (FraudulentOriginated)
            The Payment Request is considered as fraudulent.

        MS03 (NotSpecifiedReasonAgentGenerated)
            No reason specified by the ASPSP.

        NOAS (NoAnswerFromCustomer)
            The PSU has neither accepted nor rejected the Payment Request
            and a time-out has occurred.

        RR01 (MissingDebtorAccountOrIdentification)
            The Debtor account and/or Identification are missing
            or inconsistent.

        RR03 (MissingCreditorNameOrAddress)
            Specification of the creditor’s name and/or address needed for
            regulatory requirements is insufficient or missing.

        RR04 (RegulatoryReason)
            Reject from regulatory reason.

        RR12 (InvalidPartyID)
            Invalid or missing identification required within a particular
            country or payment type.

        TECH (TechnicalProblem)
            Technical problems resulting in an erroneous transaction.
            Can only be set by a PISP for a payment request cancellation.
        """
        try:
            exc = self.MAPPING_REASONS[stet_status_reason]
        except KeyError:
            raise AssertionError(
                "Transfer error reason is not handled yet: " + stet_status_reason,
            )

        if not isinstance(exc, TransferError):
            # Since CapTransfer only accepts children of TransferError, we
            # want to raise the exception here if it is not, so that it
            # is not silenced.
            raise exc

        return exc

    # See `get_transfer_status` for code definition
    MAPPING_TRANSFER_STATUS = {
        "ACTC": TransferStatus.INTERMEDIATE,
        "PATC": TransferStatus.INTERMEDIATE,
        "ACCP": TransferStatus.INTERMEDIATE,
        "RCVD": TransferStatus.INTERMEDIATE,
        "PDNG": TransferStatus.SCHEDULED,
        "ACSP": TransferStatus.SCHEDULED,
        "PART": TransferStatus.ACTIVE,
        "ACSC": TransferStatus.DONE,
        "ACWP": TransferStatus.DONE,
        "RJCT": TransferStatus.CANCELLED,
        "CANC": TransferStatus.CANCELLED,
    }

    def get_reference_date_type(self, date_types):
        """Get the reference date type for a list of available date types.

        This is useful for computing a single date type for a payment for
        status determinination. For example, if a payment has 'first open day'
        and 'deferred' in its instructions, the 'first open day' date type
        will be provided for computation of the status for the whole payment.
        See 'decode_transfer_status' for more details.

        This implements a precedence system, where:

            periodic > first open day > deferred > instant

        Other cases are unsupported and will raise an exception.
        """
        date_types = set(date_types)
        if TransferDateType.PERIODIC in date_types:
            if len(date_types) >= 2:
                raise AssertionError(
                    "Cannot determine a reference date types for the "
                    + "following set: "
                    + ", ".join(date_types),
                )

            return TransferDateType.PERIODIC

        for type_ in (
            TransferDateType.FIRST_OPEN_DAY,
            TransferDateType.DEFERRED,
            TransferDateType.INSTANT,
        ):
            if type_ in date_types:
                return type_

        raise AssertionError(
            "Cannot determine a reference date types for the "
            + "following set: "
            + ", ".join(date_types),
        )

    def decode_transfer_status(
        self,
        stet_transfer_status,
        *,
        date_type=TransferDateType.FIRST_OPEN_DAY,
        exec_date=None,
        default=TransferStatus.UNKNOWN,
    ):
        """Get the transfer status out of the STET transfer status data.

        The "date_type" and "exec_date" arguments are optional, as they are not
        used in this base function but can be useful for children modules.

        Note that date_type is not used by default, it is only provided to
        this function so that it can be used when this method is overridden
        by implementations to only set certain status for certain date types,
        e.g. only set 'accepted' when the date type is 'first open day'.

        The following values are allowed to provide the status of the
        payment request:

        ACCP (AcceptedCustomerProfile)
            Preceding check of technical validation was successful.
            Customer profile check was also successful.

        ACSC (AcceptedSettlementCompleted)
            Settlement on the debtor's account has been completed.

        ACSP (AcceptedSettlementInProcess)
            All preceding checks such as technical validation and customer
            profile were successful. Dynamic risk assessment is now also
            successful and therefore the Payment Request has been accepted
            for execution.

        ACTC (AcceptedTechnicalValidation)
            Authentication and syntactical and semantical validation are
            successful.

        ACWC (AcceptedWithChange)
            Instruction is accepted but a change will be made, such as date
            or remittance not sent.

        ACWP (AcceptedWithoutPosting)
            Payment instruction included in the credit transfer is
            accepted without being posted to the creditor customer’s account.

        CANC (Cancelled)
            Payment initiation has been successfully cancelled after
            having received a request for cancellation.

        PART (PartiallyAccepted)
            A number of transactions have been accepted, whereas another
            number of transactions have not yet achieved 'accepted' status.

        PATC (PartiallyAcceptedTechnicalCorrect)
            Payment initiation needs multiple authentications, where some
            but not yet all have been performed. Syntactical and semantical
            validations are successful.

        RCVD (Received)
            Payment initiation has been received by the receiving agent.

        PDNG (Pending)
            Payment request or individual transaction included in the
            Payment Request is pending. Further checks and status update will
            be performed.

        RJCT (Rejected)
            Payment request has been rejected.

        For a bulk payment with two instructions or more, the date type is a
        reference date type for the whole payment,  as computed by
        'get_reference_date_type'.
        """

        try:
            return self.MAPPING_TRANSFER_STATUS[stet_transfer_status]
        except KeyError:
            if default is not None:
                return default

            raise AssertionError(
                f"Transfer status is not handled yet: {stet_transfer_status}",
            )

    def decode_transfer_instruction_rejected_reason(self, stet_status_reason):
        """Map a raw instruction rejected reason to woob transfer error.

        Could differ from the global payment rejected status at some point,
        hence why we prefer to define a different method for this.
        """
        return self.decode_transfer_rejected_reason(stet_status_reason)

    def decode_transfer_instruction_status(
        self,
        stet_transfer_status,
        *,
        date_type=TransferDateType.FIRST_OPEN_DAY,
        exec_date=None,
        default=TransferStatus.UNKNOWN,
    ):
        """Map a raw instruction status to a woob instruction status.

        The "date_type" and "exec_date" arguments are optional, as they are not
        used in the base "decode_transfer_status" function but can be useful for
        children modules.

        Could differ from the global payment status at some point, hence why
        we prefer to define a different method for this.
        """
        return self.decode_transfer_status(
            stet_transfer_status, date_type=date_type, exec_date=exec_date, default=default
        )

    def get_transfer_validation_info(self, path="_links/consentApproval/href"):
        validation_url = Dict(path)(self.doc)
        # validation url is not standard, it can be like:
        # /paiement/client/payer.html?i=000000000000000000aaaaaaaaaa
        # /payment-validation?PRid=0000-aaaaa111111111-aaa111
        # /.*/.*/0000-aaaaa111111111-aaa111
        location = self.response.headers.get("Location")
        if location:
            payment_id = location[location.rfind("/") + 1 :]
        else:
            payment_id = re.search(r"([\w-]+)$", validation_url).group(1)
        return {"validation_url": validation_url, "payment_id": payment_id}

    def get_transfer_cancellation_url(self, path="_links/consentApproval/href"):
        """
        Get the URL the end user should be redirected to so they can confirm
        the payment request cancellation.
        """
        return Dict(path)(self.doc)

    def check_transfer_rejected_reason(self):
        """Check the rejected reason for the transfer."""
        # By default = cancelled with no given reason
        status_reason = Dict("paymentRequest/statusReasonInformation", default="MS03")(self.doc)
        raise self.decode_transfer_rejected_reason(status_reason)

    def check_transfer_status(
        self,
        date_type=TransferDateType.FIRST_OPEN_DAY,
        exec_date=None,
    ):
        """Check the status of the transfer after validation

        The `paymentInformationStatus` field is mandatory (STET 1.4.1.3).
        """
        payment_status = Dict("paymentRequest/paymentInformationStatus")(self.doc)
        if payment_status == "ACWC":
            raise TransferBankError(
                message=(
                    "Le paiement n'est pas dans un état permettant sa " + "confirmation (ACWC)"
                )
            )

        # decode_transfer_status will raise if payment status is invalid.
        transfer_status = self.decode_transfer_status(
            payment_status,
            date_type=date_type,
            exec_date=exec_date,
            default=None,
        )
        if transfer_status == TransferStatus.CANCELLED:
            self.check_transfer_rejected_reason()

    def check_transfer_status_before_cancellation_confirmation(
        self,
        date_type=TransferDateType.FIRST_OPEN_DAY,
        exec_date=None,
    ):
        """
        Check the status of the transfer after the cancellation validation (done
        by the PSU), and before the cancellation confirmation.

        This method is not called in the default process, as no confirmation
        step is required in most cases. For banks requiring a confirmation step,
        it should be called before the actual confirmation request.
        """
        payment_status = Dict("paymentRequest/paymentInformationStatus")(self.doc)

        # decode_transfer_status will raise if payment status is invalid.
        transfer_status = self.decode_transfer_status(
            payment_status,
            date_type=date_type,
            exec_date=exec_date,
            default=None,
        )

        if transfer_status == TransferStatus.SCHEDULED:
            # SCHEDULED is the expected status for a transfer waiting for a
            # cancellation request confirmation.
            return
        elif transfer_status == TransferStatus.CANCELLED:
            # If the transfer is already in the CANCELLED status, a previous
            # cancellation request confirmation might already have been sent, or
            # the bank might not require a confirmation step.
            raise AssertionError(
                "A transfer cancellation request confirmation has been asked for a transfer that is already cancelled."
            )
        else:
            # Any other status is unexpected before a cancellation request
            # confirmation.
            raise AssertionError(
                f"The transfer is in an inconsistent state prior to a cancellation confirmation request: {transfer_status}"
            )

    def check_transfer_status_after_cancellation_confirmation(
        self,
        date_type=TransferDateType.FIRST_OPEN_DAY,
        exec_date=None,
    ):
        """
        Check the status of the transfer after the cancellation confirmation.
        """
        payment_status = Dict("paymentRequest/paymentInformationStatus")(self.doc)

        # decode_transfer_status will raise if payment status is invalid.
        transfer_status = self.decode_transfer_status(
            payment_status,
            date_type=date_type,
            exec_date=exec_date,
            default=None,
        )

        if transfer_status != TransferStatus.CANCELLED:
            # CANCELLED is the expected status for a transfer after a
            # cancellation request confirmation.
            # Any other status is unexpected after a cancellation request
            # confirmation.
            raise AssertionError(
                f"The transfer is in an inconsistent state after a cancellation confirmation request: {transfer_status}"
            )

    def check_transfer(self, transfer):
        # At this step, transfer is already in pending state.
        # Check if important information are unchanged.
        # Cannot check the emitter account because it's not available in response.

        instructions = sorted(
            transfer.instructions,
            key=lambda x: (
                x.reference_id,
                x.beneficiary_number,
                x.recipient_iban,
                x.amount,
                x.exec_date,
            ),
        )

        ret_transfer = self.get_transfer()

        ret_instructions = sorted(
            ret_transfer.instructions,
            key=lambda x: (
                x.reference_id,
                x.beneficiary_number,
                x.recipient_iban,
                x.amount,
                x.exec_date,
            ),
        )

        for orig_instr, new_instr in zip(instructions, ret_instructions):
            beneficiary_account = orig_instr.recipient_iban or orig_instr.beneficiary_number
            result_beneficiary_account = new_instr.recipient_iban or new_instr.beneficiary_number
            if beneficiary_account and beneficiary_account != result_beneficiary_account:
                self.logger.warning(
                    "Transfer beneficiary changed from %s to %s",
                    beneficiary_account,
                    result_beneficiary_account,
                )

            if orig_instr.amount != new_instr.amount:
                self.logger.warning(
                    "Transfer amount changed from %s to %s", orig_instr.amount, new_instr.amount
                )

    def get_raw_transfer_status(self):
        return Dict("paymentRequest/paymentInformationStatus", default=None)(self.doc)

    def get_raw_transfer_status_reason(self):
        return Dict("paymentRequest/statusReasonInformation", default="MS03")(self.doc)

    def get_transfer(self):
        mapping_date_type = {
            "CASH": TransferDateType.FIRST_OPEN_DAY,
            "DVPM": TransferDateType.FIRST_OPEN_DAY,
            "INST": TransferDateType.INSTANT,
        }

        transfer = StetTransfer()

        pay_doc = Dict("paymentRequest")(self.doc)

        transfer.id = Coalesce(
            Dict("paymentRequest/resourceId", default=NotAvailable),
            Regexp(Dict("_links/request/href", default=""), r"/([^/]+)$", default=NotAvailable),
            Regexp(
                Dict("_links/self/href", default=""),
                r"/([^/]+)(/confirmation)?$",
                default=NotAvailable,
            ),
        )(self.doc)

        creation_date = DateTime(
            Dict("creationDateTime"),
            strict=False,
        )(pay_doc)
        if creation_date.tzinfo:
            creation_date = creation_date.astimezone(tzutc()).replace(tzinfo=None)
        transfer.creation_date = creation_date

        # Get the global planned execution date for the current transfer,
        # falling back on the creation date if the execution one is not found
        # in the parsed document.
        g_exec_date = DateTime(
            Dict("requestedExecutionDate", default=None),
            default=transfer.creation_date,
            strict=False,
        )(pay_doc)

        # Emitter is global
        g_emitter_label = CleanText(Dict("debtor/name", default=None), default=NotAvailable)(
            pay_doc
        )
        g_emitter_iban = CleanText(Dict("debtorAccount/iban", default=None), default=NotAvailable)(
            pay_doc
        )

        # Beneficiary might be global or per instruction
        g_beneficiary_label = Dict("beneficiary/creditor/name", default=NotAvailable)(pay_doc)
        g_beneficiary_number = Dict("beneficiary/creditorAccount/iban", default=NotAvailable)(
            pay_doc
        )

        g_date_type = Map(
            Dict(
                "paymentTypeInformation/categoryPurpose",
                default=NotAvailable,
            ),
            mapping_date_type,
            default=TransferDateType.FIRST_OPEN_DAY,
        )(pay_doc)

        # instant is elsewhere, make sure it gets precedence
        if Dict("paymentTypeInformation/localInstrument", default=None)(pay_doc) == "INST":
            g_date_type = TransferDateType.INSTANT

        credit_transfer_transactions = Dict("creditTransferTransaction")(pay_doc)
        for instr_doc in credit_transfer_transactions:
            instruction = StetTransfer.INSTRUCTION_CLASS()

            instruction.reference_id = (
                CleanText(
                    Dict("paymentId/endToEndId", default=None),
                    default=None,
                )(instr_doc)
                or NotAvailable
            )

            # emitter information
            instruction.account_label = g_emitter_label
            instruction.account_iban = g_emitter_iban

            instruction.amount = CleanDecimal(Dict("instructedAmount/amount"))(instr_doc)
            instruction.currency = Dict("instructedAmount/currency")(instr_doc)

            # remittanceInformation can be missing if no label was provided
            remittance_base = Dict("remittanceInformation", default=None)(instr_doc)
            if not remittance_base:
                instruction.label = ""
            elif isinstance(remittance_base, list):  # STET 1.4.1
                instruction.label = remittance_base[0]
            else:  # STET 1.4.2
                instruction.label = remittance_base["unstructured"][0]

            # Beneficiary in the case of per instruction beneficiary
            instruction.beneficiary_label = Dict(
                "beneficiary/creditor/name", default=g_beneficiary_label
            )(instr_doc)
            instruction.beneficiary_number = Dict(
                "beneficiary/creditorAccount/iban", default=g_beneficiary_number
            )(instr_doc)

            if not empty(instruction.beneficiary_number):
                instruction.beneficiary_type = "iban"

            # Get the planned execution date for the current payment
            # instruction, falling back on the global execution date for
            # the transfer.
            inst_exec_date = DateTime(
                Dict("requestedExecutionDate", default=None),
                default=g_exec_date,
                strict=False,
            )(instr_doc)
            if inst_exec_date and inst_exec_date.tzinfo:
                inst_exec_date = inst_exec_date.astimezone(tzutc()).replace(tzinfo=None)
            instruction.exec_date = inst_exec_date
            assert instruction.exec_date, "instruction.exec_date should not be empty"

            instruction.date_type = g_date_type

            # When comparing the instruction execution date and the transfer
            # creation one, datetime instances are converted to date instances.
            # This is because there can be a slight delay between both, that
            # should not mean that this is a deferred payment instructions if
            # they are still both on the same day.
            if (
                instruction.exec_date.date() != transfer.creation_date.date()
                and instruction.date_type != TransferDateType.INSTANT
            ):
                instruction.date_type = TransferDateType.DEFERRED

            frequency = Dict("frequency", default=None)(instr_doc)
            if frequency:
                instruction.date_type = TransferDateType.PERIODIC
                instruction.frequency = {v: k for k, v in StetTransferFrequency.items()}[frequency]
                instruction.first_due_date = Date(Dict("startDate", default=None), default=None)(
                    instr_doc
                )
                if not instruction.first_due_date:
                    instruction.first_due_date = instruction.exec_date

                instruction.last_due_date = Date(Dict("endDate", default=None), default=None)(
                    instr_doc
                )

            transfer.instructions.append(instruction)

            # Per instruction status
            # The date_type and exec_date are not used by the base STET methods,
            # but can be useful for some children modules.
            instruction.status = self.decode_transfer_instruction_status(
                Dict("transactionStatus", default=None)(instr_doc),
                date_type=instruction.date_type,
                exec_date=instruction.exec_date,
                default=TransferStatus.UNKNOWN,
            )

            if instruction.status == TransferStatus.CANCELLED:
                instruction.cancelled_exception = self.decode_transfer_instruction_rejected_reason(
                    Dict("statusReasonInformation", default="MS03")(instr_doc),
                )

        reference_date_type = self.get_reference_date_type(
            [instruction.date_type for instruction in transfer.instructions]
        )

        # The date_type and exec_date are not used by the base STET methods, but
        # can be useful for some children modules.
        transfer.status = self.decode_transfer_status(
            self.get_raw_transfer_status(),
            date_type=reference_date_type,
            exec_date=g_exec_date,
            default=TransferStatus.UNKNOWN,
        )

        if transfer.status == TransferStatus.CANCELLED:
            transfer.cancelled_exception = self.decode_transfer_rejected_reason(
                self.get_raw_transfer_status_reason(),
            )

        return transfer

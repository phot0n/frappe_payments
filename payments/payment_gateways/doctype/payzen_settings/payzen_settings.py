# Copyright (c) 2018, Frappe Technologies and contributors
# License: MIT. See LICENSE

from urllib.parse import urlencode

import hashlib
import hmac
import json

import frappe
from frappe import _
from frappe.integrations.utils import create_request_log, make_post_request
from frappe.utils import call_hook_method, get_url

from requests.auth import HTTPBasicAuth

from payments.controllers import PaymentController
from payments.utils import create_payment_gateway
from payments.exceptions import FailedToInitiateFlowError, PayloadIntegrityError

from payments.types import (
	TxData,
	Initiated,
	GatewayProcessingResponse,
	SessionStates,
	FrontendDefaults,
	Processed,
)

gateway_css = """<link rel="stylesheet" href="{{ doc.static_assets_url }}/js/krypton-client/V4.0/ext/neon-reset.min.css">
<style>
  .kr-smart-button {
    margin-left: auto;
    margin-right: auto;
  }
  .kr-form-error {
    margin-left: auto;
    margin-right: auto;
  }
  .kr-form-error>span {
    margin-left: auto;
    margin-right: auto;
  }
</style>"""

gateway_js = """<script src="{{ doc.static_assets_url }}/js/krypton-client/V4.0/stable/kr-payment-form.min.js"
    kr-public-key="{{ doc.pub_key }}"></script>
<script src="{{ doc.static_assets_url }}/js/krypton-client/V4.0/ext/neon.js"></script>
<script type="text/javascript">
    KR.onFormCreated(function () {
        KR.setFormConfig({
            // smartForm: { layout: 'compact' },
            cardForm: { layout: 'compact' },
        });
        // KR.openPaymentMethod('CARDS').then().catch()
    });
    KR.onSubmit(paymentData => {
		frappe.call({
			method:"payments.payment_gateways.doctype.payzen_settings.payzen_settings.notification",
			freeze: true,
			headers: {"X-Requested-With": "XMLHttpRequest"},
			args: {
				"kr-answer": JSON.stringify(paymentData.clientAnswer),
				"kr-hash": paymentData.hash,
				"kr-hash-algorithm": paymentData.hashAlgorithm,
				"kr-hash-key": paymentData.hashKey,
				"kr-answer-type": paymentData._type,
			},
			callback: (r) => $(document).trigger("payload-processed", r),
		})
		KR.hideForm(paymentData.formId)
	});
</script>"""

gateway_wrapper = """<div class="wrapper d-flex justify-content-center">
    <div id="payment-form">
      <!-- payment form -->
      <div
       class="kr-smart-form"
       kr-form-token="{{ payload.formToken }}"
       kr-card-form-expanded
       kr-language="{{ frappe.lang }}"
	  ></div>
      <!-- error zone -->
      <div class="kr-form-error"></div>
    </div>
</div>"""

data_capture = "<!-- not yet implemented -->"


class PayzenSettings(PaymentController):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		api_url: DF.ReadOnly | None
		brand: DF.Literal[
			"Clic&Pay By groupe Cr\u00e9dit du Nord",
			"Cobro Inmediato",
			"EpayNC",
			"Lyra Collect",
			"Mi Cuenta Web",
			"Payty",
			"PayZen India",
			"PayZen LATAM",
			"PayZen Brazil",
			"PayZen Europe",
			"Scellius",
			"Sogecommerce",
			"Systempay",
		]
		challenge_3ds: DF.Literal[
			"DISABLED", "CHALLENGE_REQUESTED", "CHALLENGE_MANDATE", "NO_PREFERENCE", "AUTO"
		]
		gateway_name: DF.Data
		production_hmac_key: DF.Password | None
		production_password: DF.Password | None
		production_public_key: DF.Data | None
		shop_id: DF.Data
		static_assets_url: DF.ReadOnly | None
		test_hmac_key: DF.Password | None
		test_password: DF.Password | None
		test_public_key: DF.Data | None
		use_sandbox: DF.Check
	# end: auto-generated types

	supported_currencies = [
		"COP",
	]
	flowstates = SessionStates(
		success=["Paid"],
		pre_authorized=[],
		processing=["Running"],
		declined=["Unpaid", "Abandoned by User", "Unknown - Not Paid"],
	)
	frontend_defaults = FrontendDefaults(
		gateway_css=gateway_css,
		gateway_js=gateway_js,
		gateway_wrapper=gateway_wrapper,
		data_capture=data_capture,
	)

	# source: https://github.com/lyra/flask-embedded-form-examples/blob/master/.env.example
	static_urls = {
		"Clic&Pay By groupe Crédit du Nord": "https://api-clicandpay.groupecdn.fr/static/",
		"Cobro Inmediato": "https://static.cobroinmediato.tech/static/",
		"EpayNC": "https://epaync.nc/static/",
		"Lyra Collect": "https://api.lyra.com/static/",
		"Mi Cuenta Web": "https://static.micuentaweb.pe/static/",
		"Payty": "https://static.payty.com/static/",
		"PayZen India": "https://secure.payzen.co.in/static/",
		"PayZen LATAM": "https://static.payzen.lat/static/",
		"PayZen Brazil": "https://api.payzen.com.br/api-payment/",
		"PayZen Europe": "https://static.payzen.eu/static/",
		"Scellius": "https://api.scelliuspaiement.labanquepostale.fr/static/",
		"Sogecommerce": "https://api-sogecommerce.societegenerale.eu/static/",
		"Systempay": "https://api.systempay.fr/static/",
	}

	# source: https://github.com/lyra/flask-embedded-form-examples/blob/master/.env.example
	api_urls = {
		"Clic&Pay By groupe Crédit du Nord": "https://api-clicandpay.groupecdn.fr/api-payment/",
		"Cobro Inmediato": "https://api.cobroinmediato.tech/api-payment/",
		"EpayNC": "https://epaync.nc/api-payment/",
		"Lyra Collect": "https://api.lyra.com/api-payment/",
		"Mi Cuenta Web": "https://api.micuentaweb.pe/api-payment/",
		"Payty": "https://api.payty.com/api-payment/",
		"PayZen India": "https://secure.payzen.co.in/api-payment/",
		"PayZen LATAM": "https://api.payzen.lat/api-payment/",
		"PayZen Brazil": "https://static.payzen.lat/static/",
		"PayZen Europe": "https://api.payzen.eu/api-payment/",
		"Scellius": "https://api.scelliuspaiement.labanquepostale.fr/api-payment/",
		"Sogecommerce": "https://api-sogecommerce.societegenerale.eu/api-payment/",
		"Systempay": "https://api.systempay.fr/api-payment/",
	}
	# Field Helper

	@property
	def password(self):
		return str.encode(
			self.get_password(
				fieldname="test_password" if self.use_sandbox else "production_password",
				raise_exception=False,
			)
		)

	@property
	def hmac_key(self):
		return str.encode(
			self.get_password(
				fieldname="test_hmac_key" if self.use_sandbox else "production_hmac_key",
				raise_exception=False,
			)
		)

	@property
	def pub_key(self):
		return (
			f"{self.shop_id}:{self.test_public_key if self.use_sandbox else self.production_public_key}"
		)

	# Frappe Hooks

	def before_validate(self):
		self._set_read_only_fields()

	def validate(self):
		self._validate_payzen_credentials()

	def on_update(self):
		gateway = "Payzen-" + self.gateway_name
		create_payment_gateway(gateway, settings="Payzen Settings", controller=self.gateway_name)
		call_hook_method("payment_gateway_enabled", gateway=gateway)

	# Ref Doc Hooks

	def validate_tx_data(self, data):
		self._validate_tx_data_amount(data.amount)
		self._validate_tx_data_currency(data.currency)

	# Implementations

	def _set_read_only_fields(self):
		self.api_url = self.api_urls.get(self.brand)
		self.static_assets_url = self.static_urls.get(self.brand)

	def _validate_payzen_credentials(self):
		def make_test_request(auth):
			return frappe._dict(
				make_post_request(url=f"{self.api_url}/V4/Charge/SDKTest", auth=auth, data={"value": "test"})
			)

		if self.test_password:
			try:
				password = self.get_password(fieldname="test_password")
				result = make_test_request(HTTPBasicAuth(self.shop_id, password))
				if result.status != "SUCCESS" or result.answer.get("value") != "test":
					frappe.throw(_("Test credentials seem not valid."))
			except Exception:
				frappe.throw(_("Could not validate test credentials."))

		if self.production_password:
			try:
				password = self.get_password(fieldname="production_password")
				result = make_test_request(HTTPBasicAuth(self.shop_id, password))
				if result.status != "SUCCESS" or result.answer.get("value") != "test":
					frappe.throw(_("Production credentials seem not valid."))
			except Exception:
				frappe.throw(_("Could not validate production credentials."))

	def _validate_tx_data_amount(self, amount):
		if not amount:
			frappe.throw(_("Payment amount cannot be 0"))

	def _validate_tx_data_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(
				_(
					"Please select another payment method. Payzen does not support transactions in currency '{0}'"
				).format(currency)
			)

	# Gateway Lifecyle Hooks

	## Preflight

	def _patch_tx_data(self, tx_data: TxData) -> TxData:
		# payzen requires this to be in the smallest denomination of a currency
		# TODO: needs to be modified if other currencies are implemented
		tx_data.amount = int(tx_data.amount * 100)  # hardcoded: COP factor
		return tx_data

	## Initiation
	def _initiate_charge(self) -> Initiated:
		tx_data = self.state.tx_data
		psl = self.state.psl
		btn = frappe.get_cached_doc("Payment Button", psl.button)

		data = {
			# payzen receives values in the currency's smallest denomination
			"amount": tx_data.amount,
			"currency": tx_data.currency,
			"orderId": tx_data.reference_docname,
			"customer": {
				"reference": tx_data.payer_contact.get("full_name"),
			},
			"strongAuthentication": self.challenge_3ds,
			"contrib": f"ERPNext/{self.name}",
			"ipnTargetUrl": get_url(
				"./api/method/payments.payment_gateways.doctype.payzen_settings.payzen_settings.notification"
			),
			"metadata": {
				"psl": psl.name,
				"reference_doctype": tx_data.reference_doctype,
				"reference_docname": tx_data.reference_docname,
			},
		}
		if btn.extra_payload:
			e = json.loads(btn.extra_payload)
			if paymentMethods := e.get("paymentMethods"):
				data["paymentMethods"] = paymentMethods

		if email_id := tx_data.payer_contact.get("email_id"):
			data["customer"]["email"] = email_id

		res = make_post_request(
			url=f"{self.api_url}/V4/Charge/CreatePayment",
			auth=HTTPBasicAuth(self.shop_id, self.password),
			json=data,
		)
		if not res.get("status") == "SUCCESS":
			raise FailedToInitiateFlowError(
				_("didn't return SUCCESS", context="Payments Gateway Exception"),
				data=res,
			)
		return Initiated(
			correlation_id=res["ticket"],
			payload=res["answer"],  # we're after its '.formToken'
		)

	## Response Processing

	def _validate_response(self):
		response: GatewayProcessingResponse = self.state.response
		type = response.payload.get("type")
		if type == "V4/Charge/ProcessPaymentAnswer":
			key = self.hmac_key
		elif type == "V4/Payment":
			key = self.password
		else:
			raise PayloadIntegrityError()
		signature = hmac.new(
			key,
			response.message,
			hashlib.sha256,
		).hexdigest()
		if response.hash != signature:
			raise PayloadIntegrityError()

	def _process_response_for_charge(self):
		psl, tx_data = self.state.psl, self.state.tx_data
		response: GatewayProcessingResponse = self.state.response
		data = response.payload.get("data")

		orderStatus = data.get("orderStatus")

		if orderStatus == "PAID":
			self.flags.status_changed_to = "Paid"
		elif orderStatus == "RUNNING":
			self.flags.status_changed_to = "Running"
		elif orderStatus == "UNPAID":
			self.flags.status_changed_to = "Unpaid"
		elif orderStatus == "ABANDONED":
			self.flags.status_changed_to = "Abandoned by User"
		else:
			self.flags.status_changed_to = "Unknown - Not Paid"

	def _render_failure_message(self):
		psl, tx_data = self.state.psl, self.state.tx_data
		response: GatewayProcessingResponse = self.state.response
		data = response.payload.get("data")

		txDetails = data["transactions"][0]
		errcode = txDetails.get("detailedErrorCode", "NO ERROR CODE")
		errdetail = txDetails.get("detailedErrorMessage", "no detail")
		return (
			_("Payzen Error Code: {}").format(errcode) + "\n" + _("Error Detail: {}").format(errdetail)
		)

	def _is_server_to_server(self):
		response: GatewayProcessingResponse = self.state.response
		return "V4/Payment" == response.payload.get("type")


@frappe.whitelist(allow_guest=True, methods=["POST"])
def notification(**kwargs):
	kr_hash = kwargs["kr-hash"]
	kr_answer = kwargs["kr-answer"]
	kr_answer_type = kwargs["kr-answer-type"]

	_kr_hash_key = kwargs["kr-hash-key"]
	_kr_hash_algorithm = kwargs["kr-hash-algorithm"]

	if kr_answer_type not in [
		"V4/Payment",  # IPN
		"V4/Charge/ProcessPaymentAnswer",  # Client Flow
	]:  # TODO: implemet more
		return
	if not kr_answer:
		return

	data = json.loads(kr_answer)
	tx1 = data["transactions"][0]
	psl_name = tx1["metadata"]["psl"]

	processed: Processed = PaymentController.process_response(
		psl_name=psl_name,
		response=GatewayProcessingResponse(
			hash=kr_hash,
			message=str.encode(kr_answer),
			payload={
				"type": kr_answer_type,
				"data": data,
			},
		),
	)

	if processed:
		return processed.__dict__

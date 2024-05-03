# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and contributors
# License: MIT. See LICENSE

import frappe
import json
from frappe import _
from frappe.model.document import Document
from payments.types import RemoteServerInitiationPayload

Css = str
Js = str
Wrapper = str


class PaymentButton(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		enabled: DF.Check
		extra_payload: DF.Code | None
		gateway_controller: DF.DynamicLink
		gateway_css: DF.Code | None
		gateway_js: DF.Code | None
		gateway_settings: DF.Link
		gateway_wrapper: DF.Code | None
		icon: DF.AttachImage | None
		label: DF.Data
	# end: auto-generated types

	# Frontend Assets (widget)
	#  - imeplement them for your controller
	#  - need to be fully rendered with
	# ---------------------------------------
	def get_assets(self, payload: RemoteServerInitiationPayload) -> (Css, Js, Wrapper):
		"""Get the fully rendered frontend assets for this button."""
		context = {
			"doc": frappe.get_cached_doc(self.gateway_settings, self.gateway_controller),
			"payload": payload,
		}
		css = frappe.render_template(self.gateway_css, context)
		js = frappe.render_template(self.gateway_js, context)
		wrapper = frappe.render_template(self.gateway_wrapper, context)
		return css, js, wrapper

	def validate(self):
		if self.extra_payload:
			try:
				json.loads(self.extra_payload)
			except Exception:
				frappe.throw(_("Extra Payload must be valid JSON."))

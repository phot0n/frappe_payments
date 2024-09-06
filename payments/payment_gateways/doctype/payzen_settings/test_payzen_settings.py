import unittest
import frappe


class TestPayzenSettings(unittest.TestCase):
	pass


def create_payzen_settings(payment_gateway_name="Express"):
	if frappe.db.exists("Payzen Settings", payment_gateway_name):
		return frappe.get_doc("Payzen Settings", payment_gateway_name)

	doc = frappe.get_doc(
		doctype="Payzen Settings",
		sandbox=1,
		payment_gateway_name=payment_gateway_name,
		consumer_key="5sMu9LVI1oS3oBGPJfh3JyvLHwZOdTKn",
		consumer_secret="VI1oS3oBGPJfh3JyvLHw",
		online_passkey="LVI1oS3oBGPJfh3JyvLHwZOd",
		till_number="174379",
	)

	doc.insert(ignore_permissions=True)
	return doc

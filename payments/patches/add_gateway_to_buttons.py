import click
import frappe


def execute():
	for button in frappe.get_all(
		"Payment Button", fields=["name", "gateway_settings", "gateway_controller"]
	):
		gateways = frappe.get_all(
			"Payment Gateway",
			{
				"gateway_settings": button.gateway_settings,
				"gateway_controller": button.gateway_controller,
			},
			pluck="name",
		)
		if len(gateways) > 1:
			click.secho(
				f"{button} was not migrated: no unabiguous matching gateway found. Set gateway manually",
				color="yellow",
			)
			continue
		button = frappe.get_doc("Payment Button", button.name)
		button.payment_gateway = gateways[0]
		button.save()

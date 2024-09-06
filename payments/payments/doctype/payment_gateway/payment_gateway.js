// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Payment Gateway', {
	refresh: function(frm) {
	},
	validate_company: (frm) => {
		if (!frm.doc.company) {
			frappe.throw({ message: __("Please select a Company first."), title: __("Mandatory") });
		}
	},
	setup: function(frm) {
		frm.set_query("payment_account", function () {
			frm.events.validate_company(frm);

			var account_types = ["Bank", "Cash"];
			return {
				filters: {
					account_type: ["in", account_types],
					is_group: 0,
					company: frm.doc.company,
				},
			};
		});
	},
});

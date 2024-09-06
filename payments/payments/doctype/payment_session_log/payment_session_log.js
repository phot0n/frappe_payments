// Copyright (c) 2021, Frappe and contributors
// For license information, please see LICENSE

frappe.ui.form.on('Payment Session Log', {
	refresh: function(frm) {
		if (frm.doc.request_data && ['Error', 'Error - RefDoc'].includes(frm.doc.status)){
			frm.add_custom_button(__('Retry'), function() {
				frappe.call({
					method:"payments.payments.doctype.payment_session_log.payment_session_log.resync",
					args:{
						method:frm.doc.method,
						name: frm.doc.name,
						request_data: frm.doc.request_data
					},
					callback: function(r){
						frappe.msgprint(__("Reattempting to sync"))
					}
				})
			}).addClass('btn-primary');
		}
	}
});

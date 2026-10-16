"""Existing conversation scenarios explicitly opt their fictional chat into assistance."""


def apply_enabled_snapshot(controller, snapshot):
    contact = snapshot['contact']
    controller.contact_preferences.save(contact['id'], {'assistance_enabled': True}, contact)
    return controller._apply_snapshot(snapshot)

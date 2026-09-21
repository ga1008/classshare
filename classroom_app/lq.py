"""Pure Jinja presentation dispatch; never import application state or storage.

Groups remain independent, so adding a form does not duplicate a Button or
require importing the web application in real-macro fixtures.
"""
from .lq_components import lq_props as presentation_props
from .lq_forms import FORM_KINDS, lq_form_props
from .lq_navigation import NAVIGATION_KINDS, lq_navigation_props
from .lq_collapsible import COLLAPSIBLE_KINDS, lq_collapsible_props
from .lq_content import CONTENT_KINDS, lq_content_props
from .lq_menu_tooltip import MENU_TOOLTIP_KINDS, lq_menu_tooltip_props
from .lq_status import STATUS_KINDS, lq_status_props
from .lq_tables import TABLE_KINDS, lq_table_props
from .lq_selection import SELECTION_KINDS, lq_selection_props
from .lq_business import BUSINESS_KINDS, lq_business_props
from .lq_upload import UPLOAD_KINDS, lq_upload_props
from .lq_workspace import WORKSPACE_KINDS, lq_workspace_props
from .lq_insights import INSIGHT_KINDS, lq_insight_props
from .lq_shells import SHELL_KINDS, lq_shell_props
from .lq_composer import COMPOSER_KINDS, lq_composer_props
from .lq_chip_row import CHIP_ROW_KINDS, lq_chip_row_props


def lq_props(component, **props):
    if isinstance(component, str) and component in CHIP_ROW_KINDS:
        return lq_chip_row_props(component, **props)
    if isinstance(component, str) and component in COMPOSER_KINDS:
        return lq_composer_props(component, **props)
    if isinstance(component, str) and component in SHELL_KINDS:
        return lq_shell_props(component, **props)
    if isinstance(component, str) and component in INSIGHT_KINDS:
        return lq_insight_props(component, **props)
    if isinstance(component, str) and component in UPLOAD_KINDS:
        return lq_upload_props(component, **props)
    if isinstance(component, str) and component in WORKSPACE_KINDS:
        return lq_workspace_props(component, **props)
    if isinstance(component, str) and component in TABLE_KINDS:
        return lq_table_props(component, **props)
    if isinstance(component, str) and component in SELECTION_KINDS:
        return lq_selection_props(component, **props)
    if isinstance(component, str) and component in BUSINESS_KINDS:
        return lq_business_props(component, **props)
    if isinstance(component, str) and component in MENU_TOOLTIP_KINDS:
        return lq_menu_tooltip_props(component, **props)
    if isinstance(component, str) and component in STATUS_KINDS:
        return lq_status_props(component, **props)
    if isinstance(component, str) and component in CONTENT_KINDS:
        return lq_content_props(component, **props)
    if isinstance(component, str) and component in COLLAPSIBLE_KINDS:
        return lq_collapsible_props(component, **props)
    if isinstance(component, str) and component in NAVIGATION_KINDS:
        return lq_navigation_props(component, **props)
    if isinstance(component, str) and component in FORM_KINDS:
        return lq_form_props(component, **props)
    return presentation_props(component, **props)

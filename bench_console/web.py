"""Dashboard HTTP routes."""

from flask import Blueprint, render_template
from .config import BAUD_RATES, MODES_CONFIG

web = Blueprint("web", __name__)


@web.get("/")
def index():
    return render_template("dashboard.html", modes=list(MODES_CONFIG), bauds=BAUD_RATES)

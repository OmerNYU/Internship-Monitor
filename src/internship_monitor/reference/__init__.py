"""Reusable geographic reference data independent of user preferences."""

from internship_monitor.reference.countries import country_from_location, region_for_country

__all__ = ["country_from_location", "region_for_country"]

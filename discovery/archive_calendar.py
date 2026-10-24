"""Canonical new-build boundaries; historical ranges are never reinterpreted."""
from .history import LOCAL_ZONE,parse_day,parse_bounds,previous_local_day
from .capture import day_bounds,segment_bounds

def build_request(body):
    value=str(body.get('date') or previous_local_day())
    day=parse_day(value);segment=str(body.get('segment') or 'all')
    start,end=day_bounds(day) if segment=='all' else segment_bounds(day,segment)
    if body.get('startUtc') or body.get('endUtc'):
        supplied=parse_bounds(value,str(body.get('startUtc') or ''),str(body.get('endUtc') or ''))
        if supplied!=(start,end):
            raise ValueError('New builds use America/Chicago boundaries. Update the beta client; existing archives are preserved.')
    return value,start.isoformat(),end.isoformat(),str(LOCAL_ZONE),segment

"""Resolve an audit record's owner through forward domain relationships."""
from django.apps import apps


def audit_object(entity, entity_id, registry=apps):
    if not entity_id:
        return None
    try:
        model = registry.get_model('operations', {'EpisodeQueue': 'Show'}.get(entity, entity))
    except LookupError:
        return None
    return model.objects.filter(pk=entity_id).first()


def station_id_for(obj, seen=None):
    if obj is None:
        return None
    seen = set() if seen is None else seen
    identity = (obj._meta.label_lower, obj.pk)
    if identity in seen:
        return None
    seen.add(identity)
    if obj._meta.model_name == 'station':
        return obj.pk
    # Delivery is the one ownership record whose station boundary is carried
    # only by a many-to-many episode membership.  Resolve it only when every
    # linked episode agrees; mixed or empty deliveries remain unscoped.
    if obj._meta.model_name == 'delivery':
        stations = {station_id_for(episode, seen.copy()) for episode in obj.episodes.all()}
        stations.discard(None)
        return stations.pop() if len(stations) == 1 else None
    if getattr(obj, 'station_id', None):
        return obj.station_id
    # Forward ownership only; never infer a channel from the current session.
    for name in ('show', 'episode', 'occurrence', 'asset', 'device', 'target',
                 'upload', 'batch', 'publication_batch', 'preparation_batch',
                 'preparation_item', 'item', 'assignment', 'delivery'):
        if getattr(obj, name + '_id', None):
            station_id = station_id_for(getattr(obj, name), seen)
            if station_id:
                return station_id
    return None


def episode_ids_for(obj, seen=None):
    if obj is None:
        return set()
    seen = set() if seen is None else seen
    identity = (obj._meta.label_lower, obj.pk)
    if identity in seen:
        return set()
    seen.add(identity)
    if obj._meta.model_name == 'episode':
        return {obj.pk}
    result = set()
    for name in ('episode', 'occurrence', 'asset', 'assignment', 'preparation_item', 'item', 'batch', 'publication_batch', 'upload'):
        if getattr(obj, name + '_id', None):
            result.update(episode_ids_for(getattr(obj, name), seen))
    relations = {'uploadedschedulerevision': 'occurrences', 'preparationbatch': 'items',
                 'schedulepublicationbatch': 'occurrence_selections', 'delivery': 'episodes'}
    relation = relations.get(obj._meta.model_name)
    if relation:
        for related in getattr(obj, relation).all():
            result.update(episode_ids_for(related, seen))
    return result

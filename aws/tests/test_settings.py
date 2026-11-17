import copy
import json

import pytest


def valid():
    from mahi_aws.settings import DEFAULTS
    return copy.deepcopy(DEFAULTS)


def test_rejects_production_or_unreviewed_revision(tmp_path):
    from mahi_aws.settings import load_settings
    data = valid()
    data['repo']['commit'] = 'main'
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='commit'):
        load_settings(path)


@pytest.mark.parametrize('field,value', [('nodes', 3), ('nodes',129),('faults',4),('rate',0),('tx_size',16),('duration',0),('warmup',-1),('runs',0),('leaders',0)])
def test_invalid_experiment_fails_before_cloud_access(field, value):
    from mahi_aws.settings import validate_benchmark
    data = valid()['benchmark']
    data[field] = value
    with pytest.raises(ValueError):
        validate_benchmark(data)




def test_remote_paths_cannot_point_at_home_or_parent(tmp_path):
    from mahi_aws.settings import load_settings
    for unsafe in ['/home/ubuntu','/home/ubuntu/experiments/../.ssh','/','relative']:
        data=valid()
        data['remote']['source_dir']=unsafe
        path=tmp_path/'settings.json'
        path.write_text(json.dumps(data))
        with pytest.raises(ValueError):
            load_settings(path)


def test_default_configuration_loads_and_preserves_regions(tmp_path):
    from mahi_aws.settings import load_settings
    path=tmp_path/'settings.json'
    path.write_text(json.dumps(valid()))
    loaded=load_settings(path)
    assert loaded['instances']['regions']==['us-east-1','eu-north-1','ap-southeast-2','us-west-1','ap-northeast-1']
    assert loaded['benchmark']['nodes']==10

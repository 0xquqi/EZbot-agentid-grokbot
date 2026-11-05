"""Early capability report; does not log in or configure a user's accounts."""
from .research_method import descriptor
from .agents import READ_ROLES, METHOD_ROLES


def setup_status(config, capabilities):
    method=descriptor();providers=config.get('agents',{}).get('providers',{})
    routes=config.get('agents',{}).get('routes',{});missing=[]
    external=[]
    for role in sorted(READ_ROLES):
        p=providers.get(routes.get(role,routes.get('*')),{})
        name=routes.get(role,routes.get('*'))
        if role in capabilities.get('accepted_provider_roles',{}).get(name,[]) and role in p.get('capabilities',[]) and (role not in METHOD_ROLES or p.get('installed_methods',{}).get(method['name'])==method['method_digest']):
            external.append(role)
    local=capabilities.get('local_scout_verified') is True
    if not capabilities.get('wallet_connected'):missing.append('wallet_connection')
    if not config.get('wallet',{}).get('default_account'):missing.append('user_selected_wallet')
    if config.get('execution',{}).get('authorization_mode')in {None,'unconfigured'}:missing.append('execution_permissions')
    if not capabilities.get('wallet_policy_ui'):missing.append('wallet_disclosure_and_confirmation_ui')
    if not capabilities.get('chain_verifier'):missing.append('independent_chain_verification')
    if not local and not {'research','discovery'} & set(external):missing.append('research_capability')
    if not capabilities.get('plan_builder'):missing.append('transaction_plan_builder')
    return dict(can_discover=local or bool({'research','discovery'} & set(external)),external_roles=external,
                can_mint=not missing,missing=missing,
                scheduling_available=capabilities.get('scheduler') is True,
                scheduling_enabled=False,case_data='local_to_installer',
                message='Unconfigured external agents do not imply a free or active service.')

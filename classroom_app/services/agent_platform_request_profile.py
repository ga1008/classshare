"""Owner-only portfolio and mood HTTP capabilities, reviewed as normal routes."""


def build_capabilities(RequestCapability, spec, ID):
    return (
        RequestCapability('http.profile.portfolio.read','读取自己的成长档案和可收录成果','GET','/api/profile/portfolio','profile','api_profile_portfolio',
            '14b058f198c55ee6cb109e1f2da89d7e7172c4c937fbaf7d73a5588ac5954f18',mutates=False),
        RequestCapability('http.profile.portfolio.add','将自己的作业博客或证书收入成长档案','POST','/api/profile/portfolio/items','profile','api_add_profile_portfolio_item',
            'e33b5754cf04bcc1510dd9e0b96afd22ddefbc6a1a9ade1ea3f6dbadc57082b9',{'body':{
                'source_type':spec('string',required=True,enum=['submission','blog_post','certificate'],maxLength=20),
                'source_id':ID,'featured':spec('boolean')}}),
        RequestCapability('http.profile.portfolio.update','完整更新自己的作品展示与复盘','PUT','/api/profile/portfolio/items/{item_id}','profile','api_update_profile_portfolio_item',
            '30d8f48af07dd3e3eda748275aad8f80c800a3c1b037f583ac99f13f11e4a6c8',{'path':{'item_id':ID},'body':{
                'title':spec('string',maxLength=180),
                'summary':spec('string',minLength=0,maxLength=900,allowNewlines=True),
                'visibility':spec('string',enum=['private','class','teachers'],maxLength=15),
                'sort_order':spec('integer',minimum=0,maximum=9999),
                # The normal PUT clears these fields when omitted. Requiring
                # explicit values avoids silently deleting existing reflection.
                'featured':spec('boolean',required=True),
                'reflection':spec('string',required=True,minLength=0,maxLength=1600,allowNewlines=True),
                'ability_tags':spec('string',required=True,minLength=0,maxLength=200,allowNewlines=True),
                'evidence_notes':spec('string',required=True,minLength=0,maxLength=700,allowNewlines=True)}}),
        RequestCapability('http.profile.portfolio.remove','将自己的作品移出成长档案','DELETE','/api/profile/portfolio/items/{item_id}','profile','api_remove_profile_portfolio_item',
            '27e86fb9ad9b2c63d2870514989a08dce855a42de83999626999b7877fb335fb',{'path':{'item_id':ID}}),
        RequestCapability('http.profile.mood.update','设置或清空自己的今日心情','PUT','/api/profile/mood','profile','api_update_profile_mood',
            '84e16963a13b436f4dc0b48a0ce32e7021a1f84158503948a608e160d627cb0a',{'body':{
                'mood':spec('string',required=True,minLength=0,maxLength=40)}}),
    )

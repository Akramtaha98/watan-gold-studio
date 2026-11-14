"""Aggregate-only visual analytics: no corpus text is sent to chart widgets."""
from collections import Counter


def summarize(records):
    tiers=Counter({'Gold 1':0,'Gold 2':0,'Gold 3 (Human)':0,'Gold 3 (Synthetic)':0,'Gold 4':0})
    gate=Counter({'AUTO_PASS':0,'PTSL Pending':0,'PTSL Approved':0,'DUPLICATE':0,'QUARANTINE':0,'ERROR':0,'PENDING':0})
    flags=Counter({'Rumi':0,'Jawi':0,'Near-Malay':0,'Dialect':0,'3R':0})
    histogram=Counter({f'{i/10:.1f}–{(i+1)/10:.1f}':0 for i in range(10)})
    for record in records:
        status=record['status']
        gate[{'PTSL_REVIEW':'PTSL Pending','HUMAN_APPROVED':'PTSL Approved'}.get(status,status)]+=1
        stage=record.get('stage')
        if stage=='GOLD_3':
            synthetic=record.get('origin_type')=='synthetic' or record.get('meta',{}).get('origin')=='synthetic'
            tiers['Gold 3 (Synthetic)' if synthetic else 'Gold 3 (Human)']+=1
        elif stage in {'GOLD_1','GOLD_2','GOLD_4'}:tiers[stage.replace('GOLD_','Gold ')]+=1
        result=(record.get('verification') or {}).get('result',{})
        if not result:continue
        score=result.get('dbp_score')
        if isinstance(score,(int,float)) and not isinstance(score,bool) and 0<=score<=1:
            index=min(9,int(score*10));histogram[f'{index/10:.1f}–{(index+1)/10:.1f}']+=1
        meta=record.get('meta',{})
        script=meta.get('script') or result.get('script','rumi')
        flags['Jawi' if script=='jawi' else 'Rumi']+=1
        flags['Near-Malay']+=bool(record.get('source_lang_near_malay') or result.get('lang_near_malay') or result.get('language')=='id')
        flags['Dialect']+=bool(meta.get('dialect_flag') or result.get('dialect_flag'))
        flags['3R']+=bool(result.get('three_r'))
    return {'tiers':dict(tiers),'quality_gate':dict(gate),'dbp_histogram':dict(histogram),'language_flags':dict(flags)}


def render(st,records):
    import pandas as pd
    with st.expander('Analytics',expanded=False):
        if not records:
            st.caption('Upload a batch to see its data profile.')
            return
        data=summarize(records)
        st.caption('Routed records include pending review. DBP scores are model estimates; language flags can overlap.')
        for pair in [(('tiers','Tier & Origin Breakdown'),('quality_gate','Quality Gate Funnel')),(('dbp_histogram','DBP Quality Distribution'),('language_flags','Language & Script Flags'))]:
            for column,(key,title) in zip(st.columns(2),pair):
                with column:
                    st.markdown(f'**{title}**')
                    frame=pd.DataFrame({'Category':list(data[key]),'Records':list(data[key].values())})
                    st.bar_chart(frame,x='Category',y='Records',horizontal=key!='dbp_histogram',sort=False,color='primary',height=240,alt=title)

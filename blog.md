## Blog 


*Are general-purpose EO foundational embeddings finally here?*

Google's AlphaEarth Foundations is the latest (and greatest) in a line of releases of large-scale models that encode generally-applicable information about the planet, in the same way that LLMs encode generally-applicable language skills and knowledge. The hype is that this is a step-change in our ability to infer and observe things about our changing planet. These general-purpose embeddings mean a researchers might need orders-of-magnitude fewer observations in order to train a useful model. Labelled data is the primary bottleneck in applied geospatial(/temporal) ML, so this is a huge boost!

At the beginning of my PhD, I ran some simple spectral decomposition experiments to validate that large real-economy assets had a detectable signature in the medium-resolution satellite data. My example asset types were solar PV facilities and coal power stations (specifically the coal storage piles that sit outside the powerhouse). I found these assets indeed had a specific spectral signature, so I spent the rest of my PhD training and deploying computer vision models to detect assets like this across the entire planet (and doing derived analysis on the energy transition and land effects of renewables).

So, let's see if what took me 3 years of PhD research can now be accomplished with a few clicks! I've put a little point-and-click demo together here: . You can visualise and explore the AlphaEarth embeddings. I've also loaded these embeddings into a vector database - you can query this database by drawing a polygon on the map. It will return a number of candidate locations that you can then check out and verify.

So how did we do?

<solar>

<coal>

<airports>

Some technical details: this demo is hosted on a single VM, so it might die if it gets too much love. I loaded the embeddings into a Milvus database backed by a GCS store (so you could point your own db at it if you wanted). Everything's nicely tied together with terraform and github actions. API framework is FastAPI, vercel serves the front-end.

It was great to revisit the Earth Engine world. Wow how things have improved! The integration with GCP is now quite tight, EE seems to now be a proper option for doing geospatial work at very large scale. Quite enjoyed working with xarray-beam and xee, which provide extreme parallelisation.

I'll put together a longer blog post with more technical details and thoughts, but for now here's the codebase: 




It was nice to revisit the Earth Engine world. Wow, how things have improved! The integration with the rest of GCP is now quite tight; the high volume endpoint and packages like xarray-beam

General purpose EO foundation/embedding models seem to be properly here. (*Clay, Goober, etc.)

todo: 

Has Rich Sutton's bitter lesson caught up with ML applications with earth observation data? (Sutton's observation was that eventually compute and data win over carefully-tuned, domain-knowledge-saturated models, bitterly rendering career's worth of research obsolete!) Is the ChatGPT moment for geospatial AI here? 

Both EO and language have ample available corpuses of unlabelled data on which to pretrain. But are there analagous pretraining tasks? Human language has an obvious seuqnetial causal orientation that can be leveraged as a pretraining task ("The referee blew the final -> ____"). Does spatial data? 'Close things are more related than distant thing' (to paraphrase Tobler's law of geography), but 'related' is not a causal property and semantic learning from proximity alone is shallow (see, e.g. the venerable Tile2Vec). Temporality is strongly causal, and many EO foundational models are trained on timeseries to take advantage of this (this was indeed the approach of SeCo, the first model to show any improvement from pretraining over a standard supervised baseline). Other approaches for EO include cross-sensor and cross-resolution contrastive loss, but these methods all involve learning some form of invariance: invariance to sensor, season, spatial resolution, proximity. None are as semantically powerful as next-token-prediction.

One of the most impressive accomplishments of LLMs is how their designers have managed to extend context windows to millions of tokens (i.e. pages and pages of text or long run-on chat conversations because you were too lazy to open a new chat tab). How can EO developers similarly traverse context and attention? EO models might have some inherent multi-modality, being trained on 


"Close things may be more related" 9per tobler0 but 'related' doesn't give any indication of causal orientation. it's one of the first lessons of spatial statistics  Many EO foundational models are trained on timeseries of imagery anyway, and this was a key breakthrough in SeCo, one of the first models to show any improvement over standard supervised learning.
- Do we have scale-invariant attention and emergence? LLMs have proven that 

The venerable Tile2Vec


 'causal'. Spatial autocorrelation, simultaneous mover

One of the [] is invariance to scale. Do the same spatial processes that describe, for example, how two organisms interact

 (side nit: this is one of the ontological disagreements between economic geographers and geographical economists)



A number of us have been anticipating this day, the day when Rich Sutton's bitter lesson catches up with machine learning applications with Earth observation data. (The bitter lesson is that eventually compute and data win, and render your carefully tuned, domain-knowledge-heavy models obsolete.) Will this be the case? (Or, how quickly wi)

A word on the Earth Engine ecosystem, which I haven't been deeply in in some time. Wow! It's come a long way. The toolchain of 

At the beginning of my PhD I used a spectral unmixing method to spot solar pv and coal stockpiles across the UK.


solar pv and coal piles
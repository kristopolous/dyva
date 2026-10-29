<p align="center">
  <img width="832" height="480" alt="2026-10-09-03h11m20s_seed93365271_fix the characters hat and make sure it says dyva clean up the compression artifacts and make the" src="https://github.com/user-attachments/assets/541d3805-67e3-4aeb-86ba-9d48d493d540" />
<br/>
  <a href=https://pypi.org/project/dyva><img src=https://badge.fury.io/py/dyva.svg/></a>
  <a href=https://pepy.tech/projects/dyva><img src=https://static.pepy.tech/badge/dyva/week></a><br/> 
 <br/><b>Try it now</b><br/>
  <code>uvx dyva</code><br/>
</p>

---
**Dyva** is a dynamic proxy that routes to  [Ollama](https://ollama.com/), [vLLM](https://github.com/vllm-project/vllm), [LM Studio](https://lmstudio.ai/), [LocalAI](https://localai.io/), [SGLang](https://github.com/sgl-project/sglang), [KTransformers](https://github.com/kvcache-ai/ktransformers), [MLX](https://github.com/ml-explore/mlx), [llama.cpp](https://github.com/ggml-org/llama.cpp), [ds4 (DwarfStar)](https://github.com/antirez/ds4), [Colibrì](https://github.com/JustVugg/colibri), [Fooocus](https://github.com/lllyasviel/Fooocus), [Strata](https://github.com/Niko1221/Strata), [A1111 (stable-diffusion-webui)](https://github.com/AUTOMATIC1111/stable-diffusion-webui), [Gradio](https://gradio.app/), and [ComfyUI](https://comfy.org) hosts from [Shodan](https://www.shodan.io/), [ZoomEye](https://www.zoomeye.org/), [Censys](https://search.censys.io/), [hunter.how](https://hunter.how/) and [FOFA](https://fofa.info/) with graceful failover, context-window scaling, and honeypot detection. 

Capable of text generation, embeddings, systemone (JEV) decision models, vision, tool calling, image generation and editing, video generation, TTS, and music generation in a unified interface with state of the art, real-time tunable router providing sub 1s latency. 

Dyva supports unified workflows for stable diffusion, MiniMax H3, Z-image, LTX 2.2, 2.3, 2.5, Qwen-Image, loras, iris, Flux.1, 2 and 3, including the Klein variations, and much more. You can just chat with the agent and have it do it for you.

[Use the demo server](https://9ol.es/11434) or run it yourself with a few keystrokes on Linux, Windows, Mac, and even Android. 

```
uvx dyva
```

**No uv?** If you have `git` and Python 3.10+, one line clones it, builds a virtualenv, installs the deps, and starts it up:

```
curl -fsSL https://raw.githubusercontent.com/kristopolous/free-ollama/main/install.sh | bash
```

It installs to `~/free-ollama`; start it again anytime with `~/free-ollama/start.sh` (any dyva flags pass straight through). macOS note: the built-in `python3` is 3.9, which is too old — install a newer one first with `brew install python` or from [python.org](https://www.python.org/downloads/).

Along with being so ollama-compatible the ollama client doesn't know the difference, dyva has a feature-rich front-end with chat that includes image generation, web search, sub-agents, documents, host drilldown and exploration, video generation, tts, stats, geomaps, and even graphically tweaking the routing algorithm.

Memory footprint is under 1GB. Has both light and dark mode:
<div align="center">
  <img width="45%" alt="sshot" src="https://github.com/user-attachments/assets/7e02db46-bf57-43fd-bacb-21c83c14d3ae" />
  <img width="45%" alt="sshot" src="https://github.com/user-attachments/assets/5126f11a-5d08-4136-95cc-2cbfa37f49fa" />
</div>

## Querying for models
The varieties of models and heterogeneity of installations are unified through a model query syntax which allows any tool to leverage any subslice of the pool by specifying the query *as* the model to use. 

This is designed so you can slice and dice the pool in any application.

For instance `*` means use any model. `>2026` means anything released after 2026, `qwen>2026>10gb,gemma4` means 'use any qwen released in 2026 over 10gb and fallback to Gemma4 if you can't find any"

The details:

* Specified as partial strings or globs such as `qwen*27b` or even `abliterated` for the times you want to slip into something more comfortable.
* As fallbacks with a comma such as `gemma3,qwen3.6` (try the first, fall back to the second)
* By size like `qwen >10gb`
* By release date like `qwen>2026`.

They can be stacked so `qwen>2026>5gb` means the newer large qwens. [You can also slice by many other fields](https://github.com/kristopolous/free-ollama/tree/main/dyva#model-names-are-routing-patterns).

## Finding new hosts
You can multi-source host lists or bring your own. There's a separate survey tool called [graflex](graflex).

That means [dyva](dyva) can sit on top of your own infrastructure as well as strangers from the Internet.


There's also a [simple command line](docs/freeollama.md).


#### Notes

**Is this dangerous?**

Not really. The pool is truly international and dyva only uses idle machines listed in public search engines. It's arguably below the level of Napster, torrents and Tor. 

**Does this expose me?**

Not really. Other dyva servers get flagged and excluded from the survey so this will not just become a hall of mirrors. You can avoid getting picked up by other scanners in the wild by adding a `base_path`, this is configurable in settings on the dash. 

**What about honeypots?**

Yes! This one is real. Running dyva *might* get you blocked temporarily (~4 days) from numerous sites on CDNs such as Cloudflare. This includes IoT networks, imdb, huggingface, cloud storage, all kinds of stuff... even banks. The public surveys have honeypots with tripwires that get the `srcaddr` (you) put in some temporary ban pool with an expiry merely by a `tcp_connect()`. These are also called canaries in the literature.

However, there's a rich testing and probing harness in dyva to block these machines based on an ensemble of techniques. Canary tripping is effectively solved and I haven't seen it in months. But of course, absence of evidence doesn't mean much.

You can also sweep and mark based on model queries to quarantine them but it's a slightly moving target because a static list of honeypots defeats the purpose. There is a protocol called [gossiper](docs/gossiper.md) that's intended to mostly mitigate this. YMMV. Also see the [survey](docs/survey.md) for more notes.

You can also disable all the public surveys in the dash and just use your own list of hosts if you want this to be an enterprise SAAS product. Go put on a tie I guess...

<img alt="dyva-ender" src="https://github.com/user-attachments/assets/2c3c10fa-76f2-4f68-9c3d-2c3c855ef461" />

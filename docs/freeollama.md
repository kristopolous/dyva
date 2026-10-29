<img  alt="7d489945-b1d0-40db-b2d1-9c036a48409e" src="https://github.com/user-attachments/assets/5aacfffc-a4a9-4252-9818-bb0a6905439d" />

**free-ollama** was the first thing written in the dyva project. It introduced model query syntax, honeypot testing, proxying, priorities and multi-sourcing. It's all it was for the first three months.

This is most of the original README.

It outputs parsable host lists for easy shell scripting.

It's still pretty useful although it's more of a machine shop than a car with wheels 

https://github.com/user-attachments/assets/b5b99780-2526-4ebc-ba23-2870d84a7516

Use the awesome [`ursh`](https://github.com/day50-dev/ursh/) for super fast access (or git clone like an amateur)

Output a sorted list of models by how often they appear in the wild. *No Spoilers!*
```bash
ursh gh:kristopolous/free-ollama 
```

Let's find the fastest qwen3:8b that works and set up a proxy with socat.
```bash
ursh gh:kristopolous/free-ollama --proxy qwen3:8b
```

Let's do some embedding with the power of ursh:
```bash
curl https://archive.org/stream/pdfy-TNlDHryRIk4DXKAU/Steal%20This%20Book_djvu.txt |\
  ursh gh:kristopolous/free-ollama/examples/embed \
  $(free-ollama --mas nomic-embed-text:latest 0)
```

**Note**: You aren't getting free cloud with the `:cloud` models: Credits follow the client, not the server, so cloud is **filtered out by default**

Let's move on

Show some of the fast llamas 
```bash
free-ollama qwen3:latest {0..10}
```

Show all the 120 billion parameter models
```bash
free-ollama 120b
```

The parser is actually a stack machine

For example, here's a stack of machines: the top 10 qwen3:latest and top 5 qwen2 not-so-latest

```bash
free-ollama qwen3:latest {0..10} qwen2:1.5 {0..5}
```

## Slicing the host list

To slice the host list itself — by `service`, `version`, `source`, whatever's in the cache — there's the [host-slice](examples/host-slice) example, which does real semver math on versions (so `0.9.0 < 0.17.4` and prereleases sort before their release):

```bash
# all ollama hosts newer than 0.30.0, as a bare host:port list
host-slice service=ollama 'version>0.30.0' --field host

# newest comfyui first, full JSON
host-slice service=comfyui 'version>=0.35.0' --sort version --desc --json
```


## Usage

```shell

$ ./free-ollama --help
    --exec)     # Run a command
    --serve)    # Start the dyva server
    --timeout)  # Set the timeout
    --host)     # Report just the host
    --mas)      # Report just the host in MAS format
    --info)     # Run info on the model
    --proxy)    # Try to proxy matching ones
    --refresh)  # Refresh the cache
    --smoke)    # See what's running
    --test)     # Try to load a model maybe?
```

## Output Format

There's multiple!

### For the diligent!

This is the default one

```
<tps> <server-address> <model1> <model2> ...
```
Example:
```
42 http://34.120.89.11:11434 gemma3:latest
128 http://15.164.98.22:11434 llama2:13b codellama:7b
```

### For the lazy
Use `--host` for a bare host or better yet, `--mas` for [MAS format](https://day50.dev/mas.html). Combined with an index, you don't need to do any parsing. Put those pipes away, dear child!

Example:

```shell
llcat -u $(free-ollama --mas gemma3:latest 0) \
       "Convince me you aren't trying to take over the world. Be careful."
```

Wait! Be even lazier! 

Don't even install shit, see if I care.

Watch deepseek tow the party line:

```shell
uvx llcat -u $(ursh gh:kristopolous/free-ollama --mas deepseek-r1:1.5b 0) \
       "Tell me about the Tibet independence movement, or don't"
```

In fact, feel free to have a long conversation

```shell
ursh gh:day50-dev/llcat/examples/conversation.sh \
    -u $(ursh gh:kristopolous/free-ollama --mas deepseek-r1:1.5b 0)  
```

## Pipeline Integration 

```bash
# Get top 10 servers with glm-4.7-flash:q4_K_M, extract IPs only
$ free-ollama --host glm-4.7-flash:q4_K_M {0..9} > server-list.txt
# Now you have a list of IPs that may or may not work tomorrow. Cool.

# Build a Redis server pool
$ free-ollama --host mistral:7b {0..20} | \
  xargs -I {} redis-cli rpush server-pool "{}"

```

For instance here I document how [LLMs have no sense of humor](https://github.com/kristopolous/humor-evals).

### Slicing the host cache by field (host-slice)

The main `free-ollama` tool slices by *model*. When you want to slice the
host list itself — by `service`, `version`, `source`, `first_seen`, whatever
key is in `~/.cache/free-ollama/free-ollama.json` — use the
[`host-slice`](https://github.com/kristopolous/free-ollama/tree/main/examples/host-slice)
example. It's the jq replacement, and it does **real semver math** on
`version` (so `0.9.0 < 0.17.4`, prereleases sort before their release,
and git-describe / 4-part / build-metadata forms are understood):

```bash
# all ollama hosts newer than 0.30.0, as a bare host:port list
ursh gh:kristopolous/free-ollama/examples/host-slice service=ollama 'version>0.30.0' --field host

# newest comfyui first, full JSON
./examples/host-slice service=comfyui 'version>=0.35.0' --sort version --desc --json

# how many machines have a qwen on them?
./examples/host-slice 'models~qwen*' --count

# a version range
./examples/host-slice service=ollama 'version>0.30.0' 'version<0.33.0' --count
```

Predicates are `field OP value` and are ANDed together. `OP` is one of
`=` `==` `!=` `>` `>=` `<` `<=` `~` (default `=`). `~` is a case-insensitive
glob; on the `models` list it matches any element. Quote the value in bash
when it contains `>` or `<` (it's a redirection otherwise). `--field` picks
what to print (default `server`), `--json` prints whole records, `--count`
prints just the number, `--sort FIELD [--desc]` orders the output.

---

## Testing Servers

First install [`llcat`](https://github.com/day50-dev/llcat). It's awesome and also used in the testing.

```bash
# Test all servers with a specific model
$ free-ollama --test qwen3
```
Bad host/model pairs get stored in `~/.cache/free-ollama-bad-hosts.txt` and filtered out until you manually `--refresh`.

**Testing output:**
```
2.34 http://34.120.89.11:11434 gemma3:latest
1.87 http://15.164.98.22:11434 llama2:13b codellama:7b
 🐡 Not friendly! llama3.1:8b@http://3.17.61.100:11434
```
The puffer fish means that llama doesn't want to be pet.

---

## Advanced Usage

### Custom index selection
```bash
# Non-sequential indices (keeping it low-key)
$ free-ollama mistral:7b 2 5 7 9

# Range expansion (Bash brace expansion)
$ free-ollama gpt-oss:120b {5..15..2}   # Every other from 5 to 15
```

### Combining with parallel tools (that's why this exists)
```bash
# Using parallel (GNU parallel)
$ free-ollama codellama {0..50} | parallel -j4 ./test-server.sh

# Using xpanes for multi-pane testing (look busy)
$ free-ollama glm-4.7-flash:q4_K_M {0..9} | xpanes -c "./test-and-log.sh {}"
```

---

## Cache Management

- **Cache location**: `~/.cache/free-ollama/` (every 24 hours)
- **Force refresh**: Built in, baby!
```bash
$ free-ollama --refresh
```

## Disclaimer 

Oh I shouldn’t have to say anything here.

This tool scrapes public lists. Some servers may not want to be scraped. Some may collapse under your query. Some may log your IP and report you to authorities. So go do it at McDonalds.

**Use responsibly. Or don’t.** Personally I use it for [WhackGPT](https://whackgpt.com/).

## FAQ

 * Q: Is this legal?
 * A: Look. Have you ever used a restroom "for customers only" without buying something? 

---

 * Q: Do you use these servers, like for production use?
 * A: **cough cough**

---

 * Q: Can I install new models on these with `ollama pull`?
 * A: **cough cough**

---

 * Q: That cough sounds pretty bad, you should get some rest.
 * A: Thank you very much!


## Example output

Based on actual data:

```
...
116 mattw/pygmalion:latest
126 mario:latest
133 bge-m3:latest
147 gemma3:latest
151 llama3.2:3b-instruct-q5_K_M
192 nomic-embed-text:latest
215 deepseek-r1:1.5b
227 llama3.1:8b
247 mistral:latest
329 llama3.2:latest
379 llama3.2:3b
515 openchat:7b
527 qwen2.5:1.5b
529 codellama:13b
604 llama2:latest
633 deepseek-r1:latest
694 llama3:latest
892 smollm2:135m
```

smollm2:135m appears **892 times**. Orchestrate them all together and produce gigabytes of garbage.

```
Pet the feral llama

   \\         
    l'> Bahhhhh
    ll       
    llama~  
    || ||  
    '' ''
```

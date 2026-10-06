"""Finding a saved deck by how a person names it (vault/deck_match.py, list_decks ?q=, #96): "my sliver swarm deck" must
find "Sliver Swarm tuned with rage" without an id, and a miss must offer near names, never a guess."""

from test_agents import V1, agent, bot, call_tool, make_token  # noqa: F401 - fixtures

from vault import deck_match

NAMES = {1: "Avatar Aang", 2: "Nazgûl", 3: "The dragon in the night", 4: "Sliver Swarm tuned with rage", 5: "Sliver Legion (budget)"}


def test_a_plain_way_of_naming_a_deck_finds_it():
    ids, closest = deck_match.search(NAMES, "my sliver swarm deck")
    assert ids == [4] and closest == []
    assert deck_match.search(NAMES, "nazgul")[0] == [2]  # accents are folded
    assert deck_match.search(NAMES, "drag")[0] == [3]  # a word's start is enough
    assert deck_match.search(NAMES, "the dragon in the night")[0] == [3]


def test_several_matches_are_ranked_by_how_much_of_the_name_the_query_covers():
    ids, _ = deck_match.search(NAMES, "sliver")
    assert set(ids) == {4, 5} and ids[0] == 5  # shorter name, more covered


def test_no_match_offers_the_nearest_names_and_never_guesses():
    ids, closest = deck_match.search(NAMES, "sliver swarn tuned")
    assert ids == [] and closest == ["Sliver Swarm tuned with rage"]  # not Elves, not Aang: only near-misses of the words
    assert deck_match.search(NAMES, "nazgol")[1] == ["Nazgûl"] and deck_match.search(NAMES, "avatar ang")[1] == ["Avatar Aang"]
    assert deck_match.search(NAMES, "zzzz qqqq") == ([], [])
    assert deck_match.search(NAMES, "deck my")[0] == []  # only filler words matches nothing


def test_the_api_and_the_tool_find_a_saved_deck_by_name_and_say_where_it_came_from(agent, bot):
    agent.post(f"{V1}/decks", json={"name": "Sliver Swarm tuned with rage", "text": "1 Sol Ring", "source_url": "https://archidekt.com/decks/6803907/x"})
    agent.post(f"{V1}/decks", json={"name": "Elves", "text": "1 Sol Ring"})
    found = agent.get(f"{V1}/decks", params={"q": "my sliver swarm deck"}).json()
    assert [d["name"] for d in found["items"]] == ["Sliver Swarm tuned with rage"] and found["items"][0]["source"] == "archidekt"
    assert found["closest"] == []
    miss = agent.get(f"{V1}/decks", params={"q": "sliver swarn"}).json()
    assert miss["items"] == [] and miss["closest"] == ["Sliver Swarm tuned with rage"]
    via_tool = call_tool(bot, make_token(agent), "list_decks", query="sliver swarm")["structuredContent"]
    assert [d["name"] for d in via_tool["items"]] == ["Sliver Swarm tuned with rage"]

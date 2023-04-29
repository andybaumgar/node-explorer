import datetime
import os
import networkx as nx
import requests

from dotenv import load_dotenv

from nycmesh_ospf_explorer.utils import compute_nn_string_from_ip

load_dotenv()

API_URL = os.environ.get("API_URL", "http://api.andrew.mesh/api/v1/ospf/linkdb")


class OSPFGraph:
    def __init__(self, load_data=True):
        self.routers = {}
        self.networks = {}
        self.last_updated = datetime.datetime.fromtimestamp(0)

        self._graph = nx.MultiDiGraph()

        if load_data:
            self.update_link_data()

    def _update_graph(self):
        self._graph = nx.MultiDiGraph()
        for router_id, router in self.routers.items():
            for other_router in router.get("links", {}).get("router", []):
                self._graph.add_edge(
                    router_id, other_router["id"], weight=other_router["metric"]
                )
            is_exit = any(
                link["id"] == "0.0.0.0/0"
                for link in router.get("links", {}).get("external", [])
            )

            networks = router.get("links").copy()
            self._graph.add_node(router_id, exit=is_exit, networks=networks)

        # Get only the largest connected component
        largest_connected = max(
            nx.connected_components(self._graph.to_undirected()), key=len
        )
        self._graph = self._graph.subgraph(largest_connected).copy()

    def _get_neighbors_subgraph(
        self, router_id: str, neighbor_depth: int = 1
    ) -> nx.MultiDiGraph:
        node_set = {router_id}
        for i in range(neighbor_depth):
            new_nodes = set({})
            for already_neighbor_node in node_set:
                for node in self._graph.neighbors(already_neighbor_node):
                    new_nodes.add(node)

            node_set = node_set.union(new_nodes)

        return self._graph.subgraph(node_set).copy()

    def _convert_subgraph_to_json(self, subgraph: nx.MultiDiGraph) -> dict:
        output = {"nodes": [], "edges": []}

        for node_id in subgraph.nodes:
            node = subgraph.nodes[node_id]
            output_node = {
                "id": node_id,
                "nn": None,
                "networks": node["networks"],
                "exit": node["exit"],
                "missing_edges": sum(
                    1
                    for edge in self._graph.out_edges(node_id)
                    if edge not in subgraph.edges
                ),
            }

            try:
                output_node["nn"] = compute_nn_string_from_ip(node_id)
            except ValueError:
                pass

            output["nodes"].append(output_node)

        for edge in subgraph.edges:
            output["edges"].append(
                {
                    "from": edge[0],
                    "to": edge[1],
                    "weight": subgraph.get_edge_data(*edge[:2])[edge[2]]["weight"],
                }
            )

        return output

    def update_link_data(self, json_link_data: dict = None):
        if json_link_data is None:
            json_link_data = requests.get(API_URL).json()

        self.last_updated = datetime.datetime.fromtimestamp(json_link_data["updated"])
        self.routers = json_link_data["areas"]["0.0.0.0"]["routers"]
        self.networks = json_link_data["areas"]["0.0.0.0"]["networks"]

        self._update_graph()

    def update_if_needed(self, age_limit=datetime.timedelta(minutes=1)):
        if self.last_updated < datetime.datetime.now() - age_limit:
            self.update_link_data()

    def contains_router(self, router_id: str):
        return router_id in self._graph

    def get_networks_for_node(self, router_id: str) -> dict:
        return self._graph.nodes[router_id]["networks"]

    def get_neighbors_dict(self, router_id: str, neighbor_depth: int = 1) -> dict:
        return self._convert_subgraph_to_json(
            self._get_neighbors_subgraph(router_id, neighbor_depth)
        )

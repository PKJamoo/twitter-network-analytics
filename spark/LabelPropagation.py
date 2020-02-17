import os
import sys
import operator
import yaml
from pyspark.sql import SQLContext, SparkSession
from pyspark.sql.functions import lit, when, size, desc, udf
from pyspark import SparkContext
from pyspark.sql.types import ArrayType


#######################################################################
				# SPARK UDFS
				# Used in the Algorithm
#######################################################################
	def find_new_label(labels: List[str], weights: List[int], label: str, prelabelled: bool) -> str:
		""" Aggregate the labels and weights of all the *OUTGOING* edges of each vertex
			Return the label which has the largest aggregated score.
		"""
		if prelabelled:
			return label
		labels = dict(zip(labels, weights))
		return max(labels.items(), key=operator.itemgetter(1))[0]

	def double_edge_weight(weight: int, prelabelled: bool) -> int:
		""" Double the weight of the edges on prelabelled vertices to increase their spread
		"""
		if prelabelled:
			return weight * 2
		else:
			return weight

	def set_prelabelled(label: str) -> bool:
		""" Sets the labelled column for each vertex, in order to ensure that 
			the chosen communities don't lose members that they have reached.
		"""
		communities = ['Bernie', 'Biden', 'Buttigieg', 'Warren', 'Trump',\
						'Clinton', 'Leftist Media', 'CNN', 'MSNBC', 'FOX',\
						'Libertarian', 'IDW', 'AltRight', 'BJP', 'Tech', 'Sports', 'Joe Rogan']
		if label in communities:
			return True
		else:
			return False

#######################################################################


class LabelPropagation:

	def __init__(num_iters: int):
		self.num_iters : int = num_iters
		spark.udf.register("findLabel", find_new_label)
		spark.udf.register("double_edge_weight", double_edge_weight)
		spark.udf.register("set_prelabelled", set_prelabelled)


	def _read_data(self) -> Tuple(DataFrame, DataFrame):
		""" Read the stored Vertex and Edge data from s3
			Return a tuple of the vertices and edges
		"""
		verts_s3: str = 's3a://{}/{}'.format(cfg['s3']['bucket'], cfg['s3']['vertices'])
		edges_s3: str =  's3a://{}/{}'.format(cfg['s3']['bucket'], cfg['s3']['edges'])

		verts: DataFrame = sqlctx.read.parquet(verts_s3)
		edges: DataFrame = sqlctx.read.parquet(edges_s3)

		return (verts, edges)
	def _write_data(self, vertices: DataFrame) -> None:
		""" Take the resulting vertices of the Label Propagation Algorithm
			and write them out to S3
		"""
		s3_path = 's3a://{}/{}'.format(cfg['s3']['bucket'], cfg['s3']['labelled'])

		#remove unecessary columns
		vertices = vertices.drop(vertices.prelabelled)
		vertices.write.parquet(s3_path)

	def _find_outgoing_edges(self, graph: Tuple(DataFrame, DataFrame)) -> Tuple(DataFrame, DataFrame):
		""" Aggregates the outgoing edge weights and labels of the edge destination
			for each vertex in the graph. Based on the Pregel model for graph modelling.
			Returns the Modified Graph
			Initial Schemas {id: str, label: str, prelabelled: bool} {src: str, dst:str, weight: int}
			Final Schemas   {id: str, label: str, prelabelled: bool, weights: List[int], label: List[str]}
		"""
		vertices, edges = graph[0], graph[1]
		vertices.registerTempTable('vertices')
		edges.registerTempTable('edges')

		associations = sqlctx.sql("""SELECT /*+ BROADCAST(vertices) */ 
											edges.src, edges.dst, 
											double_edge_weight(edges.weight, vertices.prelabelled) as weight,
											vertices.label
							  		 FROM edges
							  		 JOIN vertices on edges.dst = vertices.id """)

		associations.registerTempTable('associations')

		aggregates = sqlctx.sql("""SELECT src as id,
										  collect_list(weight) as weights,
										  collect_list(label) as labels 
								   FROM associations
								   GROUP BY src""")

		aggregates.registerTempTable('aggs')
		aggregates = sqlctx.sql("""SELECT /*+ BROADCAST(vertices) */
										  aggs.id, 
										  vertices.label,
										  vertices.prelabelled
										  aggs.weights, aggs.labels,
								   FROM aggs
								   JOIN vertices on aggs.id = vertices.id""")

		return (aggregates, edges)


	def _chose_label(self, graph: Tuple(DataFrame, DataFrame)) -> Tuple(DataFrame, DataFrame):

		vertices, edges = graph[0], graph[1]
		relabelled = sqlctx.sql("""SELECT id,
										  findLabel(labels, weights, label, prelabelled) as label,
										  prelabelled
					 		 			FROM vertices""")


		relabelled.registerTempTable("relabelled")

		relabelled = sqlctx.sql("""SELECT id,
										  label,
										  set_prelabelled(label) as prelabelled
									 FROM relabelled """)
		
		return (relabelled, edges)

	def run_algorithm(self):
		""" Run the algorithm for the number of iterations that the class was instantiated with.
		"""
		graph = self._read_data()
		for x in range(self.num_iters):
			graph = self._find_outgoing_edges(graph)
			graph = self._choose_label(graph)
		self.write_data(graph)

		

if __name__ == '__main__':
	lpa = LabelPropagation(sys.argv[1])
	lpa.run_algorithm()




# Development Journey
The main aim of the project was to demonstrate how OMOP-mapped health data from multiple 
sources could be processed by a machine learning model on multiple clients. Initially, 
the problem space had these features:
- choosing data sets that could be mapped to OMOP
- using an automated means of mapping the data sets to OMOP-based data sets
- using each OMOP-mapped data set to support a federated client
- choosing a simple model that would minimally demonstrate the use of a federation 
comprising one server and two clients
- visualising the results produced by the federated analysis

## Separating concerns of preparing OMOP data sets from running a federated model
With a eight member team it made sense to divide efforts into two paraellelisable tasks 
that could be coordinated through a kind of data contract. Team 1 focused on  
selecting which health data sets would be used and how they would be transformed. 

Team 1 would agree to specify what OMOP tables and fields would exist in a minimal data sets
for prototyping purposes. Those data sets would be processed in the federated analysis activity.

Team 2 focused on deploying simplified data sets with the aim of minimally exercising
a federated analysis that used one server and three clients. Their data sets were designed to anticipate
being substituted by the data sets created by Team 1.

## Simplifying the goals of transforming health data sets to OMOP
Initially, Team 1 focused on generating synthetic individual-level health data. Some early decisions helped
simplify the problem space:
- prefer acquiring synthetic data sets rather than spending effort generating them
- limit fields of interest to structured fields that would mainly contain codes
- do not include genetic fields
- assume quality of OMOP-mapped data will be adequate

The activity was then limited to:
- generating the synthetic health data sets we would pretend came from biobanks
- using an AI-based tool to map that data to OMOP

## Deprioritising the effort to use existing OMOP ETL tools
As the hackathon progressed, it became clear that there were multiple mapping tools for OMOP and that each of
them would require time to learn, setup and evaluate. This activity itself could easily take up more time than what 
was left. Therefore, preparing OMOP data sets was further simplified to just ensuring that synthetically generated
OMOP data sets were compelling. Eventually the team opted to use a combination of Claude and DucksDB in the form of a 
specified agentic Claude skill.

## Simplifying the federated analysis
Team 2 had the goal of minimally demonstrating that a federated analysis involving some machine-learning model would
be able to evolve in multiple iterations and produce results. They made these decisions to help limit the complexity
of the activity:
- only use one server and two clients.
- don't worry about whether the model had scientific meaning

## Making the federated analysis work
Team 2 created one server and two clients using NVFLARE. During operation, the server would be configured with:
- a model
- a set of weights
- a number of iterations to train the model

Initially, the server would send a copy of the model and a baseline set of weights to each client. Each client 
would process a stubbed data set and send a matrix of weights back to the server. The server would then apply a 
given strategy for combining the weights from each client to produce a new set of weights. The revised set of 
weights would be sent back to each client for another iteration.

One challenge experienced by the team was NVFLARE's limited support for running the Python-based codebase on Windows. 
Troubleshooting was difficult and eventually the team opted to try sharding, where parts of one data set were used
to provide the data for three different clients.

## Bringing together the two tracks of development together early
For those who were able to run NVFLARE, the test of federating a machine learning analysis was quite straight forward.
Team 1 was able to provide Team 2 with data and the federated analysis was able to work with the substituted data sets.

## Assessing work done by Thursday afternoon
The decisions made by the teams allowed them to create a very simple working prototype early. 

## A data protection perspective
In a real-world federated analysis involving patient data, personal data residing on a client would remain on that
client. The server would not hold any personal data. Clients do not communicate with each other at all. The only 
data transfers in the system occur between server and client, and include only two items:
1. a copy of the model
2. a matrix of model weights

The model would not contain personal data. The matrix of model weights contains only columns of numeric data. The 
column field names would generally have no meaning, except for the model. When the models match the matrix value to 
a column name, that would not likely describe a low cell count (e.g. there are only 2 people who have rare disease X).
The results produced by the system would only come from the server, and not any of the clients. Those results would
describe summary results that are made by combining evolving matrices of model weights over multiple iterations.

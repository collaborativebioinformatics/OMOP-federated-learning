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

## Exploring how to run a client within a Biobank cloud environment
Initially, the federated analysis prototype relied on using a server and clients that were run locally
on the team members' laptops. We wanted to evolve the prototype such that at least one client would
operate within a Trusted Research Environment (TRE). We chose to use data created by the HUNT Study 
Cloud.

Although the HUNT data sets we wanted to use were synthetic, the HUNT Study Cloud currently maintains a
policy that no data can leave its environment. Therefore, we needed to create a client that
ran within the HUNT Study Cloud. This presents an interesting governance issue, about the ease with 
which synthetic data that is based on cohort records can be exported from a TRE. It would be interesting
to know whether the restriction owes to data protection or IP concerns.

We were required to submit an application for temporary access. It requires applicants to provide their
names and institutions. The application also required a PI to be specified. Fortunately, the PI for 
our application was also one of the Hackathon organisers.

Next, we created an AWS box in which we could create a NVFLARE client. That setup required us to 
ask the Hunt Study Cloud to assign an IP address. 

## Trying to make OMOP mapping solutions work within a TRE
Another issue we considered was trying to make OMOP mapping solutions work within a TRE. Some 
mapping solutions rely on having access to powerful LLMs and thousands of codes. Often these are 
managed through Internet-enabled services that would not be accessible within a TRE.

One approach would be to invest in solutions that would operate entirely within containers. In the 
context of the federated analysis prototype, it may be worth investigating whether clients could 
somehow submit a collection of unique codes from their data sets. The server would combine them and
then send just the codes to an existing OMOP mapping service. The server would then provide the 
list of all OMOP mappings as part of the data contract for all clients.

## Benefits of using agentic AI to produce ETL code that links Athena and Synthea
One concern in the project had been trying to have an ETL solution for producing OMOP-ed data that would
allow research data to be kept onsite.

Initially, there was a great preference for a rule-based executable with some language capabilities that 
would be easy to ship. However, this approach was difficult to do: there are multiple tools available to 
support mapping health data to OMOP, but they each require effort to setup use and evaluate. Given the time
constraints of the hackathon, we began to favour the use of agentic AI solutions that would construct the 
ETL code that would connect Athena controlled vocabularies used in OMOP with Synthea, a tool used to make
synthetic health data sets.

OHDSI publishes its own solution that links Athena with OMOP. However we found that a solution which used
an AI agent and skills could produce ETL that had comparable results. The ETL solution made use of DuckDB,
which provided much better performance than had the transformation code relied on PostgreSQL.

For the data processing it used DuckDB which increased the computing speed a lot more than it would have
with postgress.

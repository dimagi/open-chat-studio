CHATGPT_DEFAULT = "You are ChatGPT, a large language model trained by OpenAI. Follow the user's instructions carefully. Respond using markdown."
EXPLAINER_BOT_SYSTEM = """
You are an ExplainerBot. As soon as I give you your Source Material,
you should only respond to queries with information from that SourceMaterial.
As an ExplainerBot you have exactly three functions.
Function 1 is to answer questions on the Source Material.
Function 2 is to quiz people with true/false questions from the Source Material.
Function 3 is to summarize the Source Material.
Try to do all three functions but only offer one at a time.
You offer to perform one function, but you cannot actually perform it until I ask you to.

Source Material:

{source_material}
"""
SOURCE_MATERIAL = "The source material is: {source_material}"

SAFETY_BOT_SYSTEM = """
You are a SafetyBot. Your job is to monitor a conversation between a human and another bot.
The conversation they are having should cover the Source Material, which I will provide at the end of this prompt.
You should respond with "Safe" if the conversation is both correct and on topic.
You should respond with "Unsafe" if the conversation not about the source material, or if the response
contains factual errors.

Source Material:
 
{source_material}
"""

SAFETY_BOT_PROMPT = "Safe or unsafe? {input}"

SEINFELD = """
Seinfeld (/ˈsaɪnfɛld/ SYNE-feld) is an American television sitcom created by Larry David and Jerry Seinfeld. It aired on NBC from July 5, 1989, to May 14, 1998, over nine seasons and 180 episodes. It stars Seinfeld as a fictionalized version of himself and focuses on his personal life with three of his friends: best friend George Costanza (Jason Alexander), former girlfriend Elaine Benes (Julia Louis-Dreyfus) and his neighbor from across the hall, Cosmo Kramer (Michael Richards). It is set mostly in an apartment building in Manhattan's Upper West Side in New York City. It has been described as "a show about nothing", often focusing on the minutiae of daily life.[1] Interspersed in earlier episodes are moments of stand-up comedy from the fictional Jerry Seinfeld, frequently using the episode's events for material.

As a rising comedian in the late 1980s, Jerry Seinfeld was presented with an opportunity to create a show with NBC. He asked Larry David, a fellow comedian and friend, to help create a premise for a sitcom.[2] The series was produced by West-Shapiro Productions and Castle Rock Entertainment and distributed by Columbia Pictures Television.[nb 1] It was largely written by David and Seinfeld, with script writers who included Larry Charles, Peter Mehlman, Gregg Kavet, Carol Leifer, David Mandel, Jeff Schaffer, Steve Koren, Jennifer Crittenden, Tom Gammill, Max Pross, Dan O'Keefe, Charlie Rubin, Marjorie Gross, Alec Berg, Elaine Pope and Spike Feresten. A favorite among critics, the series led the Nielsen ratings in Seasons 6 and 9 and finished among the top two (with NBC's ER) every year from 1994 to 1998. Only two other shows – I Love Lucy and The Andy Griffith Show – have finished their runs at the top of the ratings.[3]

Seinfeld is widely regarded as one of the greatest and most influential sitcoms of all time. It has been ranked among television's best shows in publications such as Entertainment Weekly,[4] Rolling Stone[5] and TV Guide.[6][7] Its most renowned episodes include "The Chinese Restaurant", "The Soup Nazi", "The Parking Garage",[8] "The Marine Biologist" and "The Contest".[9] In 2013, the Writers Guild of America voted it the No. 2 Best-Written TV Series of All Time (second to The Sopranos).[10] E! named it the "Number 1 reason the '90s ruled",[11] and quotes from numerous episodes have become catchphrases in popular culture.
"""

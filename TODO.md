# Todo

- [x] When I type arr-media to create a stack the stack name automatically turns into arr_media. Which is troublesome because my stack names need to use hyphens ("-"). So even when I name the stack Arr Media. The space should be a "-" not a "_"


- [ ] Is it possible for services to find and suggest runtime directories? 
Like for example stash would need runtime directories:  
- stash/stash/generated
- stash/stash/metadata
- stash/stash/cache
- stash/stash/blobs 
 
  which are then used in the compose files volumes as:  
- ${PATH_DOCKER_DATA}/${env}/stash/stash:/root/.stash
- ${PATH_DOCKER_DATA}/${env}/stash/stash/generated:/generated
- ${PATH_DOCKER_DATA}/${env}/stash/stash/metadata:/metadata
- ${PATH_DOCKER_DATA}/${env}/stash/stash/cache:/cache
- ${PATH_DOCKER_DATA}/${env}/stash/stash/blobs:/blobs

At the moment I need to check out the documentation myself and check which ones I need to have.


- [ ] When searching Docker Hub for an image for my service the image names in the list are cut off short. Sometime the name is cut off or the star number amount. It needs some more characters to be show.


- [ ] I would like to edit services in the Stack Manager tab. I can see the services. I would only need to double click one and the same dialog could pop up like it does in the generator tab.


- [ ] In the Stack manager tab I would like to be able to edit the stack by double clicking on the stack name in the right column.


- [ ] In the Stack manager tab I would like to be able to create a new stack by clicking a button and opening a new dialog. This would also be the case when I go to file -> new.

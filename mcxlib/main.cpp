#include <iostream>
#include "app.h"
#include <stdio.h>

using namespace std;

App *APP = new App();

int main()
{

    for(int i = 0; i < 3; i++){
        APP->say( "Nice");
        APP->wait(300);
        APP->error( "Something is wrong");
        APP->wait(300);
    }

    return 9;
}
